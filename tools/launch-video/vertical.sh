#!/usr/bin/env bash
# A 17-second 9:16 cut of the launch video for TikTok, Reels and Shorts.
# Usage, from the repo root:
#   bash tools/launch-video/vertical.sh docs/media/clixy-launch.mp4 clixy-vertical.mp4
set -e
IN=${1:-docs/media/clixy-launch.mp4}
OUT=${2:-clixy-vertical.mp4}
F=assets/fonts
L=$F/Manrope-Light.ttf

seg() { # name start end crop_w crop_h crop_x crop_y line1 line2
  local fade_at
  fade_at=$(python3 -c "print($3 - $2 - 0.03)")
  echo "[0:v]trim=start=$2:end=$3,setpts=PTS-STARTPTS,crop=$4:$5:$6:$7,scale=1080:-2:flags=lanczos,pad=1080:1920:0:(oh-ih)/2+40:color=0x0a0a12,drawtext=fontfile=$L:text='$8':fontsize=84:fontcolor=white:x=(w-text_w)/2:y=330,drawtext=fontfile=$L:text='$9':fontsize=84:fontcolor=0xff5a3d:x=(w-text_w)/2:y=440[$1v];[0:a]atrim=start=$2:end=$3,asetpts=PTS-STARTPTS,afade=t=in:d=0.02,afade=t=out:st=$fade_at:d=0.03[$1a];"
}

FC="$(seg g 17.0 23.0 1200 880 320 200 'it does your geometry' 'right on your screen')"
FC="$FC$(seg d 10.0 15.0 1200 880 560 170 'hold ctrl + win' 'ask. it draws.')"
FC="$FC$(seg b 23.5 26.0 1080 900 420 40 'say hi to' 'your pixel buddies')"
FC="$FC$(seg e 35.0 38.5 1080 700 120 250 'free and open source' 'for windows')"
FC="$FC[gv][ga][dv][da][bv][ba][ev][ea]concat=n=4:v=1:a=1[cv][ca];"
FC="$FC[1:v]scale=110:110[logo];[cv][logo]overlay=x=(W-110)/2-190:y=1560[cl];"
FC="$FC[cl]drawtext=fontfile=$F/Manrope-ExtraLight.ttf:text='clixy':fontsize=96:fontcolor=white:x=(w-text_w)/2+40:y=1555,drawtext=fontfile=$F/JetBrainsMono-Regular.ttf:text='github.com/RaySam07/clixy':fontsize=34:fontcolor=0xffd23f:x=(w-text_w)/2:y=1720[outv]"

ffmpeg -y -loglevel error -i "$IN" -loop 1 -i assets/clixy-logo.png -filter_complex "$FC" \
  -map "[outv]" -map "[ca]" -t 17 -r 60 -c:v libx264 -preset slow -crf 17 -pix_fmt yuv420p \
  -c:a aac -b:a 256k -movflags +faststart "$OUT"
