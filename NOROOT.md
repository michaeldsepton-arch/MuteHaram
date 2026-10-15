# No-root test: put the hook inside the app (LSPatch / NPatch)

Goal of this test: prove our hook runs inside ReVanced YouTube (and CineHD) **without unlocking or rooting the phone**. Nothing is wiped. Takes ~15–30 minutes. No backup needed (but see the one warning in Part 2).

How it works, in one line: a patcher app copies YouTube, glues our hook into the copy, and you install the copy. The copy is the only app affected.

If this fails, `ROOTING.md` is the fallback.

## Part 1 — Get the three apps on the phone

Download these in the phone's browser and install each (allow "install unknown apps" for the browser if asked):

1. **LSPatch manager** (the patcher): github.com/JingMatrix/LSPatch → Releases → latest (v1.2) → `manager-v1.2-...-release.apk`.
   - *Backup plan if it fails on your Android version:* **NPatch**, github.com/7723mod/NPatch → Releases → `manager-v1.0.9-...-release.apk`. Same steps below.
2. **Our hook**: https://github.com/michaeldsepton-arch/MuteHaram/releases/latest → `hook-debug.apk`. Install it. (It has no icon/screen — that's normal. It will show in your app list as "MuteHaram Hook" only in settings.)
3. The app to patch: your existing **ReVanced Extended YouTube** (already installed).

## Part 2 — Save things before you replace the app (2 min)

The patched app has a different signature, so Android makes you **uninstall the old one first**. That erases its data.
- In ReVanced Extended: Settings → ReVanced Extended → **Import / export settings** → export to a file (keep it in Downloads).
- Your watch history/subscriptions live in your Google account, so they come back when you sign in.
- Keep the ReVanced YouTube **APK file** handy (or re-download it from whatever source you used). The patcher can also read the installed app directly, which is easier.

## Part 3 — Patch it

1. Open the **LSPatch** app. Go to the **Manage** / **Patch** screen (the "+" button).
2. Choose the app to patch → **Select from installed apps** → pick ReVanced YouTube. (Or "Select storage APK" if you saved the file.)
3. Pick the patch mode **Integrated** (sometimes labelled "embed modules into the app"). This is the important setting: Integrated bakes our hook into the copy so no root/manager is needed to run it.
4. Under **Embedded modules**, add **MuteHaram Hook**.
5. Leave the other options at default. Tap **Start patch**. Wait for "Success".
6. The patcher says where the new APK is, or offers an **Install** button. Before you tap install:
   - Uninstall the original ReVanced YouTube (long-press the icon → App info → Uninstall).
   - Then install the patched one.

If it errors with "split APK" or "bundle": ReVanced from "Manager" is usually one file; if yours is several, tell me the error text.

## Part 4 — Check the hook fired

You need the PC for this (one time).

1. Phone: Settings → About phone → tap **Build number** 7 times → Settings → System → Developer options → turn on **USB debugging**. Plug into the PC and tap **Allow**.
2. PC: download Google's **SDK Platform-Tools** (official developer.android.com page), unzip to `C:\platform-tools`. In PowerShell:
   ```powershell
   cd C:\platform-tools
   .\adb devices
   .\adb logcat -c
   .\adb logcat | findstr MuteHaramHook
   ```
3. On the phone open the patched YouTube, sign in, and **play any video with sound**.
4. You want to see lines like:
   ```
   MuteHaramHook: Hooking AudioTrack in <package name>
   MuteHaramHook: Hooked write(ByteBuffer, int, int)
   ```
   That's success: the hook is inside the app without root.

## Part 5 — Then CineHD

Repeat Parts 3–4 with CineHD. Also run this and send me the answer (it fixes a placeholder in the code):
```powershell
.\adb shell pm list packages | findstr /i cine
.\adb shell pm list packages | findstr /i youtube
```

## What to send me

- Whether you saw the `Hooking AudioTrack` line for YouTube and for CineHD.
- Your Android version and build number.
- Any error text from the patcher or from `logcat` (copy/paste or a photo).
- The two package-name lists above.

## If it goes wrong

| Symptom | Meaning / fix |
|---|---|
| Patcher fails | Try NPatch instead, or send me the error |
| Patched app crashes on open / "app not installed" | Old app still installed (uninstall first), or the app checks its own signature (CineHD might). Tell me which |
| Google sign-in breaks in ReVanced | It uses microG for login; make sure microG is installed and try signing in again. Tell me what you see |
| App works but no `MuteHaramHook` lines | Hook didn't load: confirm Integrated mode and that MuteHaram Hook was added as an embedded module. Or the app plays audio a way we don't hook yet (that's useful info too, send the log) |
| Play Protect warning | Expected for modified apps. Choose "Install anyway" |

## Undo

Uninstall the patched app and reinstall the original ReVanced. Phone settings and system stay untouched.

---

## Part 6 — Video-sync test (the key experiment)

The hook can delay the sound by a set amount and tell the player "my audio clock is that far behind", so the video should wait too. Test it with a fake delay (no AI model needed).

1. Install the newest `hook-debug.apk` (latest release), then **re-patch** YouTube with it (Part 3) so the app embeds the new version. Check logcat shows `FIRST CALL` lines when a video plays.
2. With the phone plugged in, set the delay **before** starting a video:
   ```powershell
   .\adb shell setprop debug.muteharam.delay_ms 1000
   ```
3. Play a video of someone talking (a close-up works best). Watch the lips vs. the voice.
   - Works: voice matches lips, the video starts about 1 second late.
   - Doesn't work: the voice is about 1 second behind the lips.
4. Control test: `.\adb shell setprop debug.muteharam.noclock 1`, restart the video. Now the voice SHOULD lag the lips. If it does, test 3 proves the trick.
5. Turn everything off: `.\adb shell setprop debug.muteharam.delay_ms 0` and `.\adb shell setprop debug.muteharam.noclock 0`.

Changing the delay while a video is playing glitches. Set it, then (re)start the video.
