---
name: task manager
apps: taskmgr.exe
names: task manager
words: cpu, processor, cpu load, ram, memory usage, gpu, disk usage, processes, end task, not responding, frozen, kill, startup apps, efficiency mode, performance, running apps, power usage, uptime, slow
---
- open it straight away: ctrl+shift+esc, or [OPEN:taskmgr]. it opens on its default start page (processes unless changed in its settings).
- it often runs as administrator. when the open-windows list marks it admin, clixy can't list its buttons or see clicks in it: point with a box from the screenshot, say any further click in the same sentence, and add [DONE] once what they asked for is on screen.

## pages (down the left; the ☰ at the top left shows their names)
- processes: every app's cpu, memory, disk, network and gpu use; click a column heading to sort by it; the power usage and power usage trend columns show what drains the battery
- performance: live graphs for cpu (load %, speed, up time, cores), memory, disk, wi-fi or ethernet, and gpu; click one on the left to see it big
- app history · startup apps (turn apps off so they don't start with windows, with their startup impact) · users · details (every process by name and pid) · services

## tricks
- end a frozen app: select it on processes, then end task at the top, or right-click it > end task
- efficiency mode: right-click a process > efficiency mode, so it uses less power
- the taskbar or desktop is stuck: processes > windows explorer > restart
- its settings page (bottom left) sets the default start page (so it can always open on performance), always on top and the update speed
- resmon opens resource monitor for each app's disk and network use (it may ask for admin permission)
