"""
Run a .tflite on the saved reference input (CPU, in the litert venv) and compare
its spectrogram output with the PyTorch one: relative error and correlation.

Usage (venv with ai-edge-litert):
  python check_tflite.py model.tflite ref_1040.npz
"""
import sys, time
import numpy as np
from ai_edge_litert.interpreter import Interpreter

model, ref = sys.argv[1], np.load(sys.argv[2])
it = Interpreter(model_path=model)
it.allocate_tensors()
inp = it.get_input_details()[0]
x = ref["x_padded"].astype(np.float32).reshape(1, -1)
assert tuple(inp["shape"]) == x.shape, (inp["shape"], x.shape)
it.set_tensor(inp["index"], x)
t0 = time.time()
it.invoke()
print(f"invoke {time.time()-t0:.1f}s")
outs = sorted(it.get_output_details(), key=lambda o: o["index"])
re, im = (it.get_tensor(o["index"]) for o in outs)
for name, got, want in (("real", re, ref["re"]), ("imag", im, ref["im"])):
    got, want = got.reshape(want.shape), want
    err = np.linalg.norm(got - want) / (np.linalg.norm(want) + 1e-9)
    corr = np.corrcoef(got.ravel(), want.ravel())[0, 1]
    print(f"{name}: rel.err {err:.4f}  corr {corr:.5f}  nan={np.isnan(got).sum()}")
