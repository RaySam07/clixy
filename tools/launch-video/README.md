# Launch video and X banners

`docs/media/clixy-launch.mp4` (40 s, 1920x1080, 60 fps) and the X headers in
`docs/brand/` are drawn by these scripts from Clixy's own code: the side
island, the overlay and its drawing engine, and the pixel buddies. The music
is synthesized by `beat.py`, so there is nothing to license.

Needs ffmpeg, plus `scipy` and `Pillow` on top of `requirements.txt`.

```bash
export QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2
python tools/launch-video/launch.py video.mp4 events.json      # frames, ~2 min
python tools/launch-video/beat.py events.json beat.wav         # the track, synced to events.json
ffmpeg -i video.mp4 -i beat.wav -c:v copy -c:a aac -b:a 256k -shortest clixy-launch.mp4

python tools/launch-video/xbanner.py coral docs/brand          # or: ink

bash tools/launch-video/vertical.sh docs/media/clixy-launch.mp4 docs/media/clixy-vertical.mp4
```

`vertical.sh` cuts the 17-second 9:16 version for TikTok, Reels and Shorts:
geometry first, then the spreadsheet, the buddies and the end card.

Scenes are classes in `launch.py` with their start and end times; the copy
on each card is plain text in them (the flex cards are in `CARDS`).
