---
name: windows
apps:
names: windows
words: shortcut, keyboard, window, desktop, screenshot, clipboard, emoji, lock, run, snap, virtual desktop, quick settings, notification, battery, cpu, wi-fi, display, storage, uninstall, startup
---
- the fastest route wins: [OPEN:...] a settings page, tool or app; then a shortcut; clicking through menus last.
- the windows key alone opens start; typing straight away searches apps, settings and files.

## shortcuts
- win+i settings · win+e file explorer · win+r the run box · win+s search
- win+x the power user menu: installed apps, power options, event viewer, system, device manager, network connections, disk management, terminal, task manager, settings
- win+a quick settings: wi-fi, bluetooth, airplane mode, volume, brightness, night light, energy saver (battery saver before 24h2), accessibility
- win+n notifications and the calendar · win+d show the desktop · win+l lock · win+m minimise everything
- win+tab task view · alt+tab switch windows · win+1 to win+9 open or switch to that taskbar app
- win+left or win+right snaps a window to half the screen · win+up maximises · win+down restores, then minimises · win+z snap layouts
- win+shift+left or win+shift+right moves the window to the other monitor
- win+ctrl+d new virtual desktop · win+ctrl+left or right switches desktops · win+ctrl+f4 closes this one
- win+shift+s snips part of the screen to the clipboard · win+prtsc saves the whole screen to pictures\screenshots · alt+prtsc copies the window in front
- win+v clipboard history (the first time, it offers to turn it on) · win+. emoji, gifs and symbols
- win+h voice typing in any text box · win+k cast to a wireless display · win+p project to another screen
- win+g xbox game bar · win+alt+r records the screen in games and most apps
- ctrl+shift+esc opens task manager straight away · ctrl+alt+del lock, switch user, sign out, task manager
- win+ctrl+shift+b restarts the graphics driver when the screen freezes or goes black
- win+u accessibility settings · win+plus magnifier, win+esc closes it · win+ctrl+enter narrator
- alt+f4 closes the window in front; on the desktop it opens the shut down box
- shift+f10 opens the right-click menu from the keyboard; shift+right-click opens the full older menu (show more options)

## run box (win+r) and windows tools
- taskmgr task manager · msinfo32 full system information (model, processor, ram, bios) · dxdiag graphics details · winver the exact windows version and build
- devmgmt.msc device manager · diskmgmt.msc disk management · services.msc services · eventvwr.msc event viewer · taskschd.msc task scheduler
- appwiz.cpl uninstall programs · ncpa.cpl network adapters · mmsys.cpl sound playback and recording devices · main.cpl mouse · powercfg.cpl power plans and what the lid and power button do · sysdm.cpl system properties (pc name, remote, environment variables under advanced)
- control /name Microsoft.PowerOptions, Microsoft.DeviceManager, Microsoft.CredentialManager or Microsoft.ProgramsAndFeatures opens that control panel page
- shell:startup the startup folder · shell:downloads · %temp% temporary files · %appdata%
- cleanmgr disk cleanup · mdsched memory test at the next restart · osk on-screen keyboard · charmap · mstsc remote desktop

## power and battery
- battery level, power mode (best power efficiency, balanced, best performance), energy saver and battery use by app: settings > system > power and battery, [OPEN:ms-settings:batterysaver]
- battery health (design capacity against what it holds now, and recent use): [OPEN:battery report], windows' own powercfg battery report, opened in the browser
- live cpu, memory, disk and gpu load: task manager's performance page; which apps use the most power: the power usage column on its processes page
- if power mode is missing from settings, a power plan other than balanced is chosen in powercfg.cpl

## storage and apps
- free space and cleanup recommendations: settings > system > storage, [OPEN:ms-settings:storagesense]
- uninstall: settings > apps > installed apps, [OPEN:ms-settings:appsfeatures], then the ··· beside the app
- apps that start with windows: [OPEN:ms-settings:startupapps], or task manager's startup apps page

## network
- wi-fi, bluetooth and airplane mode on or off: win+a
- the saved wi-fi password: settings > network and internet > wi-fi, the network's properties, then view next to wi-fi security key (windows 11 22h2 and later)
- network adapters: ncpa.cpl · network reset: settings > network and internet > advanced network settings > network reset

## display
- brightness: the slider in win+a · scale, resolution, orientation, multiple displays: [OPEN:ms-settings:display] · refresh rate: [OPEN:ms-settings:display-advanced]
- night light: win+a, or [OPEN:ms-settings:nightlight]
