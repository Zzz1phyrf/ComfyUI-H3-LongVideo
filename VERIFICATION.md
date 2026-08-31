# Verification

Last verified: 2026-08-31 on Windows 11, Python 3.12 and ComfyUI portable.

## Passed

- Candidate package: 58 automated tests passed.
- Installed package: the same 58 automated tests passed.
- Frontend JavaScript passed `node --check`.
- ComfyUI started successfully and loaded `H3LVUnified` plus the settings and director-rules routes.
- Faster-Whisper 1.2.1 imported from the active ComfyUI Python.
- A real 10-second Chinese speech sample was transcribed in the worker subprocess on CUDA with word timestamps.
- The default singing movement rhythm remained `right, right, left, left` after save/reset validation.
- Same-direction lateral segments accept a motion-matched cut; repairable left/right three-quarter crossings return through front instead of aborting analysis.
- The current workflow contains no saved developer ASR Python or model path.

## Not claimed by these checks

- Automated tests do not prove final MiniMax H3 visual quality, clothing consistency, lip-sync quality or invisible joins.
- Final acceptance still requires real Ref2VA renders and manual review with the tester's model, LoRA, reference images and audio.
