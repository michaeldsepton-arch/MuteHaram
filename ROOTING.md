# Rooting the Pixel 9 Pro — beginner walkthrough (Windows PC)

Goal: end up with **Magisk** (root) + **LSPosed/Vector** (lets our hook run inside YouTube/CineHD), then install `hook-debug.apk`.
Time: about 1–1.5 hours, mostly waiting on downloads. Do it in order. Don't skip the checks.

## Read this first

- **Unlocking the bootloader ERASES THE WHOLE PHONE** (photos, apps, texts, everything). Back up first (Part 0).
- Google officially allows unlocking, so this does not void anything permanently or break the phone. You can undo all of it by flashing stock files again.
- After rooting, some apps (banking, Google Wallet tap-to-pay, some streaming) may refuse to work or complain. If the phone is your daily driver, know this before you start.
- Use a good USB cable, plug straight into the PC (not a hub), and keep the phone above 50% battery.
- Words you'll see: **bootloader** = the tiny program that starts the phone. **fastboot** = a PC tool that talks to it. **image / .img** = a file that gets written to the phone. **flash** = write that file to the phone.
- Our phone: Pixel 9 Pro, codename **caiman**. If a guide says "Pixel 9", it's the wrong device — stick with caiman.

---

## Part 0 — Back up and prepare the phone

1. Back up photos (Google Photos), contacts, and anything you can't lose. The phone will be wiped in Part 2.
2. Write down your Google account password (you'll sign in again after the wipe).
3. Update the phone fully first: **Settings → System → Software updates**. Then note your exact build:
   **Settings → About phone → Build number** (looks like `BD3A.250905.014`). **Write it down.**
4. Turn on developer options: **Settings → About phone**, tap **Build number** 7 times, enter your PIN.
5. **Settings → System → Developer options**, turn on:
   - **USB debugging**
   - **OEM unlocking** (if greyed out: connect to Wi-Fi, wait a few minutes, make sure no work/school account is on the phone, and try again).

## Part 1 — Set up the PC

1. Make a folder `C:\platform-tools`.
2. Download **SDK Platform-Tools for Windows** from Google's official page (search "Android SDK Platform-Tools", the site is developer.android.com) and unzip it so `adb.exe` and `fastboot.exe` are inside `C:\platform-tools`.
3. Install Google's USB driver if Windows doesn't recognise the phone later (search "Google USB Driver", official developer.android.com page).
4. Open **PowerShell** and run:
   ```powershell
   cd C:\platform-tools
   .\adb devices
   ```
   Plug the phone in. On the phone, tap **Allow** on the "Allow USB debugging?" popup (tick "Always allow"). Run the command again. You should see a line with `device` next to a code. If it says `unauthorized`, accept the popup. If nothing shows, try another cable/port.

## Part 2 — Unlock the bootloader (THIS WIPES THE PHONE)

1. In PowerShell:
   ```powershell
   .\adb reboot bootloader
   ```
   The phone shows a screen with a robot and small text ("fastboot mode").
2. Check the PC sees it:
   ```powershell
   .\fastboot devices
   ```
   You should see a code and the word `fastboot`.
3. Unlock:
   ```powershell
   .\fastboot flashing unlock
   ```
4. On the phone, use the **volume keys** to pick **Unlock the bootloader**, press **power** to confirm. It erases and reboots. This takes a few minutes.
5. Set the phone up again quickly (skip most things). Redo Part 0 steps 4–5 (developer options → **USB debugging** on; OEM unlocking will now be on and greyed out — that's right). Run `.\adb devices` and accept the popup again.

## Part 3 — Get the right boot file

Magisk works by patching one small file from Google's own firmware, so you need the firmware that matches your phone's build exactly.

1. Go to Google's **Factory Images for Nexus and Pixel** page (search for it; it's on developers.google.com).
2. Find **caiman** (Pixel 9 Pro — not "komodo" or "tokay").
3. Pick the entry matching your build number from Part 0. Download the zip. (Big, ~2–3 GB. Use only Google's official page.)
4. Unzip it. Inside is a second zip named like `image-caiman-<build>.zip`. Unzip that too.
5. Find **`init_boot.img`** inside. Copy it to your phone's **Download** folder (USB file transfer, or `.\adb push init_boot.img /sdcard/Download/`).

## Part 4 — Patch it with Magisk (on the phone)

1. On the phone, open a browser and download the **Magisk app** from its official GitHub: `github.com/topjohnwu/Magisk` → Releases → the `Magisk-vXX.X.apk`. Install it (allow "install unknown apps" for the browser when asked).
2. Open Magisk. Tap **Install** (next to "Magisk") → **Select and Patch a File** → pick `init_boot.img` from Download.
3. Tap **Let's go**. When done, it says "All done" and saves `magisk_patched-XXXXX.img` in Download.
4. Copy that file to the PC:
   ```powershell
   .\adb pull /sdcard/Download/magisk_patched-XXXXX.img .
   ```
   (Replace XXXXX with the real name. Tip: rename it `patched.img` to make typing easy.) **Only use the file you patched yourself on this phone, never one from the internet.**

## Part 5 — Flash it

1. ```powershell
   .\adb reboot bootloader
   .\fastboot flash init_boot patched.img
   .\fastboot reboot
   ```
2. The phone restarts normally. Open the **Magisk** app. It should show **Installed** with a version number. That means you're rooted.
3. **If the phone won't boot** (loops on the logo): hold power + volume down to get back to the bootloader, then flash the *original* `init_boot.img` (from Part 3) the same way: `.\fastboot flash init_boot init_boot.img`, then `.\fastboot reboot`. Back to normal, no harm done.

## Part 6 — Turn on Zygisk

LSPosed needs it.
1. Magisk app → **Settings** (gear icon) → turn on **Zygisk**.
2. Reboot the phone.

## Part 7 — Install LSPosed (now called "Vector" in newer versions)

1. Check the project's official GitHub releases page: search "JingMatrix LSPosed" (it has been renamed **Vector** recently — use whichever the official repo's latest release says, and get the **Zygisk** zip, not the "Riru" one). Make sure it supports your Android version (Android 16 supported in v1.10.2 and later).
2. Download the zip on the phone.
3. Magisk app → **Modules** tab → **Install from storage** → pick the zip → wait → **Reboot**.
4. After reboot, you'll get an LSPosed notification (or an app icon). Open the **LSPosed manager**.

## Part 8 — Install our hook

1. Download `hook-debug.apk` from https://github.com/michaeldsepton-arch/MuteHaram/releases/latest and install it.
2. LSPosed manager → **Modules** → tap **MuteHaram Hook** → turn it **on**.
3. Under the module's scope, tick **YouTube** (ReVanced Extended) and **CineHD**.
4. Force-stop those apps (or reboot).

## Part 9 — Check it works (this is what to send me)

1. Find CineHD's real package name:
   ```powershell
   .\adb shell pm list packages | findstr /i cine
   ```
   and the YouTube one (ReVanced Extended may have a different name than stock):
   ```powershell
   .\adb shell pm list packages | findstr /i youtube
   ```
2. Watch the hook's log while you play a video in each app:
   ```powershell
   .\adb logcat | findstr MuteHaramHook
   ```
   You want lines like `Hooking AudioTrack in ...` and `Hooked write(...)`.
3. **Send me:** the package names, the log lines, and your Android version/build. With those I fix the placeholder `com.cinehd.app` and we move on.

---

## Stuck? Common problems

| Problem | Fix |
|---|---|
| `adb devices` shows nothing | Different cable/port; install Google USB driver; re-accept the popup |
| `fastboot devices` shows nothing | Phone must be in bootloader screen; try another USB port (USB-A 2.0 ports are often better) |
| "OEM unlocking" greyed out | Wi-Fi on, wait, remove work/school accounts, check for carrier lock |
| Magisk says "Not installed" | You flashed the wrong file/partition. Pixel 9 Pro uses **init_boot**, not boot |
| Bootloop after LSPosed | Boot to safe mode (hold volume down at boot) or reflash stock `init_boot.img` |
| App won't open / banking complains | Expected with root; ask me about Play Integrity workarounds if needed |

## Undo everything

Flash the original `init_boot.img` (Part 5 step 3) to remove root. To relock the bootloader later: `.\fastboot flashing lock` (wipes the phone again).

*Sources used: community guides on XDA ("Rooting your Pixel 9 Pro"), awesome-android-root's Pixel guide, and the LSPosed/Vector project README. Re-check Magisk's and LSPosed's GitHub release pages for current versions before starting; I couldn't confirm the very latest numbers.*
