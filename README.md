# Features
- Display any image or GIF without size or length limits on the LCD screen of a compatible Corsair AIO.
- LED ring control with automatically calculated colors based on the currently loaded image.
- Saving and loading of the last image position, size, etc.
- Autostart which works fine on Windows, haven't tested it much on Linux.
- Low resources usage compared to iCUE.

# Usage
- Install [Python](https://www.python.org/downloads/ "Python") 3.6 or newer.
- Install [OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB "OpenRGB").
- Clone the repo or download as ZIP and extract it to a new folder.
- Install the required modules using `pip install -r requirements.txt`.
- Run the script.

# Tested devices
- Corsair iCUE H170i ELITE LCD (non-XT), 0x1b1c / 0x0c39

# FAQ
- I don't want to control the LEDs with the script / use OpenRGB, how to disable the warning popup?
	- Delete `led_controller_openrgb.py`, the rest of the script will work fine without it.

# Why?
- Mostly because I was bored, and I don't want to keep iCUE installed just to be able to change the displayed image on the LCD. The script is 99% made by ChatGPT, so bugs / other issues can happen anytime as this is my first Python project which I started without any prior knowledge. Use at your own risk, I take no responsibility for any damage caused to your AIO, which is highly unlikely.

# Thanks to
- [browserdotsys](https://github.com/browserdotsys "browserdotsys") for [the gist](https://gist.github.com/browserdotsys/ef1b22c60c31d9c61e18cca30b3ce903 "the gist") that's used as a base of this script to communicate with the AIO.

