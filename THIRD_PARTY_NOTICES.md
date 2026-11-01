# Third-party notices

Clixy is MIT-licensed (see [LICENSE](LICENSE)). It includes portions of the
open-source projects below, used under their licenses, whose notices are
reproduced here as those licenses require.

## Earlier open-source work Clixy builds on

### Clicky

Copyright (c) 2026 Farza. MIT License. <https://github.com/farzaa/clicky>

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### A Windows port of Clicky

Copyright (c) 2026 Abhishek Vulla. MIT License.

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Grid locator (`locator.py`)

Adapted from Bitshank-2338/clicky-windows (`ai/universal_locator.py`), MIT License.

## Fonts

Manrope and JetBrains Mono, under the SIL Open Font License 1.1. The license
texts ship with the fonts in [`assets/fonts/`](assets/fonts).

## Libraries in the Windows build

`Clixy.exe` and `Clixy-Setup.exe` bundle the Python packages listed in
[`requirements.txt`](requirements.txt), each under its own license. Of note:
**PyQt6** is licensed under the GNU GPL v3, so the bundled builds are
distributed under the GPL v3's terms for that component; the complete source
for every build is this repository. The local speech models (faster-whisper,
Kokoro) download on first use under their own licenses.
