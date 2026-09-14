# Installing Golden Finds on the shop laptop

Takes about 15 minutes, once. Needs internet for the install only.

## 1. Install Python

1. Go to **python.org → Downloads → Windows** and download the latest Python.
2. Open the installer. On the first screen, **tick "Add python.exe to PATH"**.
3. Click **Install Now**.

## 2. Get Golden Finds

1. Sign in to **github.com** and open the `golden-finds-pos` repository.
2. Click the green **Code** button → **Download ZIP**.
3. Right-click the ZIP → **Extract All** → extract to `C:\GoldenFinds`.

## 3. Install

1. Open `C:\GoldenFinds` (the folder that has `Install Golden Finds.bat` in it).
2. **Double-click `Install Golden Finds.bat`**.
3. If Windows says "Windows protected your PC", click **More info → Run anyway**.
4. Wait until it says **Golden Finds is installed**. The till opens in the browser.
5. Create Mum's account on the first screen, then add the shopkeeper from **Admin → Staff**.

Done. The installer has:

- put a **Golden Finds** shortcut on the desktop
- set the till to **start by itself** when the laptop turns on
- scheduled a **backup every night at 9pm** (or as soon as the laptop is next on),
  into OneDrive or Google Drive if the laptop has one, otherwise into Documents

## Every day, for Mum

Turn on the laptop → double-click **Golden Finds** → tap her name → PIN → sell.

## Updating later

1. Download the new ZIP and extract it **over** `C:\GoldenFinds`, replacing files when asked.
2. Double-click `Install Golden Finds.bat` again.

The shop's data lives in `golden-finds-pos\instance` and is never replaced by an update.
The installer backs it up first anyway.

**Never delete the `instance` folder.** That is the shop.

## If there is no internet at the shop

On a computer with internet, inside the project folder, run:

```
golden-finds-pos\.venv\Scripts\pip download -r golden-finds-pos\requirements.txt -d wheels
```

Copy the whole folder, including `wheels`, to the laptop on a flash drive. The installer
uses `wheels` automatically. Don't copy `.venv`, `instance` or `backups` from your computer.

## Removing it

Double-click `golden-finds-pos\windows\Uninstall Golden Finds.bat`. This removes the
shortcuts, startup and nightly backup. It does **not** delete the shop's data.
