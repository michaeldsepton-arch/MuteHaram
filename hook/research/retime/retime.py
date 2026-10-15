"""
Retime a fixed-shape .tflite audio model: change the number of STFT frames T
(the time axis) without re-converting from PyTorch.

Why: the GPU-ready TIGER model on Hugging Face is fixed at 12 s chunks. Its
weights don't depend on the chunk length, only the tensor shapes (and a few
shape constants: RESHAPE targets, SLICE sizes, RESIZE sizes) do. This script
re-derives all of those for a new T by running shape inference through the
graph, then patches the flatbuffer IN PLACE (same sizes, only values change).

Self-test: run with --frames-new equal to --frames-old; every shape must come
out unchanged.

Usage (venv with `tflite`, `numpy`):
  python retime.py in.tflite out.tflite --frames-old 1040 --frames-new 176
"""
import argparse, sys
import numpy as np
import tflite as T

BO = {v: k for k, v in vars(T.BuiltinOperator).items() if not k.startswith("_")}
PAD_SAME, PAD_VALID = 0, 1


def opts(cls, op):
    t = op.BuiltinOptions()
    o = cls()
    o.Init(t.Bytes, t.Pos)
    return o


# Filled in main(): old time-axis size at each pyramid level -> new size.
TLEVELS = [{}]


def prod(xs):
    p = 1
    for x in xs:
        p *= int(x)
    return p


def bcast(a, b):
    a, b = list(a), list(b)
    n = max(len(a), len(b))
    a = [1] * (n - len(a)) + a
    b = [1] * (n - len(b)) + b
    out = []
    for x, y in zip(a, b):
        if x == y or y == 1:
            out.append(x)
        elif x == 1:
            out.append(y)
        else:
            raise ValueError(f"broadcast {a} vs {b}")
    return out


def conv_out(n, k, s, d, pad):
    if pad == PAD_SAME:
        return -(-n // s)
    return (n - d * (k - 1) - 1) // s + 1


def reshape_align(I, O, In):
    """Old input dims I, old output dims O, new input dims In -> new output dims."""
    n, m = len(I), len(O)
    i = j = 0
    out_new = [None] * m
    while i < n or j < m:
        gi, gj = [], []
        pi = po = 1
        if i < n:
            gi.append(i); pi *= I[i]; i += 1
        if j < m:
            gj.append(j); po *= O[j]; j += 1
        while pi != po:
            if pi < po and i < n:
                gi.append(i); pi *= I[i]; i += 1
            elif po < pi and j < m:
                gj.append(j); po *= O[j]; j += 1
            else:
                raise ValueError(f"reshape groups don't align: {I}->{O}")
        while i < n and I[i] == 1:
            gi.append(i); i += 1
        while j < m and O[j] == 1:
            gj.append(j); j += 1
        newP = prod(In[k] for k in gi)
        if len(gj) == 1:
            out_new[gj[0]] = newP
            continue
        # split: dims that equal a changed input dim take its new value
        mapping = {}
        for k in gi:
            if In[k] != I[k]:
                mapping.setdefault(I[k], []).append(In[k])
        vals = []
        for jj in gj:
            d = O[jj]
            if d in mapping and mapping[d]:
                vals.append(mapping[d].pop(0))
            else:
                vals.append(d)
        if prod(vals) != newP:
            # A merged input dim (e.g. 33*1040) was split again: recognise the
            # time-length levels (T, T/2, ... and +4 conv padding) among the outputs.
            lv = TLEVELS[0]
            vals = [lv.get(O[jj], O[jj]) for jj in gj]
        if prod(vals) != newP:
            raise ValueError(f"reshape split ambiguity: {I}->{O} new {list(In)} group {gi}->{gj} vals {vals} need {newP}")
        for jj, v in zip(gj, vals):
            out_new[jj] = v
    return out_new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--frames-old", type=int, required=True)
    ap.add_argument("--frames-new", type=int, required=True)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    buf = bytearray(open(a.src, "rb").read())
    m = T.Model.GetRootAsModel(buf, 0)
    g = m.Subgraphs(0)
    nT = g.TensorsLength()

    def tshape(i):
        return [int(x) for x in g.Tensors(i).ShapeAsNumpy()]

    def const_i32(ti):
        """Writable int32 view of a constant tensor's data, or None."""
        t = g.Tensors(ti)
        b = m.Buffers(t.Buffer())
        if b.DataLength() == 0:
            return None
        return b.DataAsNumpy().view(np.int32)

    orig_cache = {}
    written = {}

    def orig_i32(ti):
        """Original (unpatched) values of a small int32 constant."""
        if ti not in orig_cache:
            c = const_i32(ti)
            orig_cache[ti] = None if c is None else c.copy()
        return orig_cache[ti]

    def write_i32(ti, vals):
        """Patch a constant; constants shared by several ops must agree."""
        vals = [int(v) for v in vals]
        if ti in written and written[ti] != vals:
            raise ValueError(f"shared constant {ti} needs two different values: {written[ti]} vs {vals}")
        written[ti] = vals
        const_i32(ti)[:] = vals

    old = [tshape(i) for i in range(nT)]
    new = [None] * nT
    def has_data(i):
        b = m.Buffers(g.Tensors(i).Buffer())
        return b.DataLength() > 0 or b.Size() > 0  # big constants live outside the buffer vector

    is_const = [has_data(i) for i in range(nT)]
    for i in range(nT):
        if is_const[i]:
            new[i] = list(old[i])

    Told, Tnew = a.frames_old, a.frames_new
    lvl = {}
    for sh in range(0, 5):
        for pad in (0, 4):
            lvl[Told // (2 ** sh) + pad] = Tnew // (2 ** sh) + pad
    TLEVELS[0] = lvl

    # --- input ---
    inp = int(g.InputsAsNumpy()[0])
    S_old = old[inp][-1]
    k = a.frames_new
    S_new = S_old - (a.frames_old - 1) * 512 + (k - 1) * 512
    new[inp] = list(old[inp][:-1]) + [S_new]
    print(f"input {old[inp]} -> {new[inp]}")

    patched = 0
    for oi in range(g.OperatorsLength()):
        op = g.Operators(oi)
        code = m.OperatorCodes(op.OpcodeIndex())
        name = BO.get(max(code.BuiltinCode(), code.DeprecatedBuiltinCode()), "?")
        ins = [int(x) for x in op.InputsAsNumpy()]
        outs = [int(x) for x in op.OutputsAsNumpy()]
        o0 = outs[0]

        def nin(j):
            s = new[ins[j]]
            assert s is not None, f"op {oi} {name}: input {j} has no new shape"
            return s

        try:
            if name in ("SQRT", "RSQRT", "LOGISTIC", "RELU", "SOFTMAX", "NEG", "ABS", "TANH", "EXP", "LOG",
                        "QUANTIZE", "DEQUANTIZE", "CAST", "PRELU_UNUSED", "LEAKY_RELU", "HARD_SWISH", "GELU"):
                res = [list(nin(0))]
            elif name in ("ADD", "SUB", "MUL", "DIV", "MAXIMUM", "MINIMUM", "POW", "SQUARED_DIFFERENCE"):
                res = [bcast(nin(0), nin(1))] if len(ins) > 1 else [list(nin(0))]
            elif name == "RESHAPE":
                I, O, In = old[ins[0]], old[o0], nin(0)
                if prod(In) == prod(I) and In == I:
                    res = [list(O)]
                else:
                    res = [reshape_align(I, O, In)]
                    if len(ins) > 1:
                        c = orig_i32(ins[1])
                        if c is not None:
                            ev = [int(x) for x in c]
                            nv = [(-1 if ev[q] == -1 else res[0][q]) for q in range(len(ev))]
                            if ev != nv:
                                write_i32(ins[1], nv); patched += 1
                    ro = op.BuiltinOptions()
                    if ro is not None and op.BuiltinOptionsType() == T.BuiltinOptions.ReshapeOptions:
                        ropt = opts(T.ReshapeOptions, op)
                        if ropt.NewShapeLength() > 0:
                            arr = ropt.NewShapeAsNumpy()
                            ev = list(arr)
                            arr[:] = [(-1 if ev[q] == -1 else res[0][q]) for q in range(len(ev))]
            elif name == "TRANSPOSE":
                perm = [int(x) for x in orig_i32(ins[1])]
                res = [[nin(0)[p] for p in perm]]
            elif name in ("SUM", "MEAN", "REDUCE_MAX", "REDUCE_MIN"):
                axes = [int(x) for x in orig_i32(ins[1])]
                keep = opts(T.ReducerOptions, op).KeepDims()
                s = nin(0)
                r = len(s)
                axes = [x % r for x in axes]
                res = [[(1 if q in axes else s[q]) for q in range(r)] if keep
                       else [s[q] for q in range(r) if q not in axes]]
            elif name == "SLICE":
                beg, size = orig_i32(ins[1]), orig_i32(ins[2])
                s_old, s_new = old[ins[0]], nin(0)
                r = len(s_new)
                out = []
                nsize = [int(x) for x in size]
                for q in range(r):
                    if s_old[q] == s_new[q]:
                        out.append(int(size[q]) if size[q] != -1 else s_new[q] - int(beg[q]))
                    else:
                        if size[q] == -1:
                            out.append(s_new[q] - int(beg[q]))
                        else:
                            nsize[q] = int(size[q]) + (s_new[q] - s_old[q])
                            out.append(nsize[q])
                        if out[-1] < 1 or int(beg[q]) + out[-1] > s_new[q]:
                            raise ValueError(f"slice out of range axis {q}: begin {beg[q]} size {out[-1]} of {s_new[q]}")
                if nsize != [int(x) for x in size]:
                    write_i32(ins[2], nsize); patched += 1
                res = [out]
            elif name == "CONCATENATION":
                ax = opts(T.ConcatenationOptions, op).Axis()
                shapes = [nin(j) for j in range(len(ins))]
                ax %= len(shapes[0])
                out = list(shapes[0])
                out[ax] = sum(s[ax] for s in shapes)
                for s in shapes:
                    assert all(s[q] == out[q] for q in range(len(out)) if q != ax), (shapes, ax)
                res = [out]
            elif name in ("CONV_2D", "DEPTHWISE_CONV_2D"):
                cls = T.Conv2DOptions if name == "CONV_2D" else T.DepthwiseConv2DOptions
                o = opts(cls, op)
                s = nin(0)
                f = old[ins[1]]
                if name == "CONV_2D":
                    kh, kw, cout = f[1], f[2], f[0]
                else:
                    kh, kw, cout = f[1], f[2], f[3]
                dh, dw = o.DilationHFactor(), o.DilationWFactor()
                res = [[s[0], conv_out(s[1], kh, o.StrideH(), dh, o.Padding()),
                        conv_out(s[2], kw, o.StrideW(), dw, o.Padding()), cout]]
            elif name == "AVERAGE_POOL_2D":
                o = opts(T.Pool2DOptions, op)
                s = nin(0)
                res = [[s[0], conv_out(s[1], o.FilterHeight(), o.StrideH(), 1, o.Padding()),
                        conv_out(s[2], o.FilterWidth(), o.StrideW(), 1, o.Padding()), s[3]]]
            elif name == "PAD":
                pads = orig_i32(ins[1]).reshape(-1, 2)
                s = nin(0)
                res = [[s[q] + int(pads[q][0]) + int(pads[q][1]) for q in range(len(s))]]
            elif name == "RESIZE_NEAREST_NEIGHBOR":
                size = [int(x) for x in orig_i32(ins[1])]
                s_old, s_new = old[ins[0]], nin(0)
                nsz = list(size)
                for q, ax in enumerate((1, 2)):
                    if s_old[ax] != s_new[ax]:
                        ratio, rem = divmod(size[q], s_old[ax])
                        assert rem == 0, f"non-integer resize ratio {size[q]}/{s_old[ax]}"
                        nsz[q] = ratio * s_new[ax]
                if nsz != size:
                    write_i32(ins[1], nsz); patched += 1
                res = [[s_new[0], nsz[0], nsz[1], s_new[3]]]
            elif name == "FULLY_CONNECTED":
                o = opts(T.FullyConnectedOptions, op)
                w = old[ins[1]]
                s = nin(0)
                if o.KeepNumDims():
                    res = [list(s[:-1]) + [w[0]]]
                else:
                    res = [[prod(s) // w[1], w[0]]]
            elif name == "BATCH_MATMUL":
                o = opts(T.BatchMatMulOptions, op)
                x, y = nin(0), nin(1)
                M = x[-1] if o.AdjX() else x[-2]
                N = y[-2] if o.AdjY() else y[-1]
                res = [bcast(x[:-2], y[:-2]) + [M, N]]
            else:
                raise NotImplementedError(name)
        except Exception as e:
            print(f"FAILED at op #{oi} {name}: {e}")
            print("  inputs old:", [old[x] for x in ins], " new:", [new[x] for x in ins])
            print("  outputs old:", [old[x] for x in outs])
            sys.exit(1)

        for ti, s in zip(outs, res):
            new[ti] = [int(x) for x in s]
        if a.verbose and oi % 2000 == 0:
            print(f"op {oi}/{g.OperatorsLength()} {name} {[old[x] for x in outs]} -> {res}")

    # --- self-consistency: unchanged-T run must reproduce old shapes ---
    if a.frames_new == a.frames_old:
        bad = [i for i in range(nT) if new[i] != old[i]]
        print(f"self-test: {len(bad)} tensors differ from the original shapes")
        for i in bad[:10]:
            print("  tensor", i, old[i], "->", new[i])
        if bad:
            sys.exit(1)

    # --- scalar constants that average over the time axis (1/T at each pyramid level) ---
    avg_patched = 0
    if a.frames_new != a.frames_old:
        lv = TLEVELS[0]
        for i in range(nT):
            t = g.Tensors(i)
            b = m.Buffers(t.Buffer())
            if b.DataLength() == 0 or b.DataLength() > 4 or prod(old[i]) != 1:
                continue
            if t.Type() == T.TensorType.FLOAT16:
                arr = b.DataAsNumpy().view(np.float16)
            elif t.Type() == T.TensorType.FLOAT32:
                arr = b.DataAsNumpy().view(np.float32)
            else:
                continue
            v = float(arr[0])
            if v <= 0:
                continue
            inv = 1.0 / v
            for L, Ln in lv.items():
                if L in (Told // (2 ** q) for q in range(5)) and abs(inv - L) / L < 2e-3:
                    arr[0] = 1.0 / Ln
                    avg_patched += 1
                    if a.verbose:
                        print(f"  mean constant tensor {i}: 1/{L} -> 1/{Ln}")
                    break
        print(f"patched {avg_patched} time-averaging constants")

    # --- write all shapes ---
    for i in range(nT):
        t = g.Tensors(i)
        if new[i] is None:
            continue
        arr = t.ShapeAsNumpy()
        if len(arr) != len(new[i]):
            print("rank mismatch tensor", i, old[i], new[i]); sys.exit(1)
        arr[:] = new[i]
        if t.ShapeSignatureLength() > 0:
            sig = t.ShapeSignatureAsNumpy()
            sig[:] = new[i]
    open(a.dst, "wb").write(buf)
    out_t = int(g.OutputsAsNumpy()[0])
    print(f"patched {patched} shape constants; output {old[out_t]} -> {new[out_t]}; wrote {a.dst}")


if __name__ == "__main__":
    main()
