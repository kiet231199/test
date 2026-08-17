# PSNR comparison policy

Type: grilling
Status: open
Blocked by: 01

## Question

Fix the media-comparison semantics so the same inputs give the same verdicts as the legacy tools. Per "Unified Excel schema", checks work on `output.*` and `reference.*` files found in the case directory, and media-check computes PSNR. This ticket decides how those files are prepared and compared:

- Alignment/padding crop: encoder outputs padded to alignment (legacy Encode `crop_gst_padding.py`), Allegro width alignment to 4 pixels (legacy `analyze.sh`) - when cropping applies, to what dimensions, and how the post-processing is applied to `output.*` before metrics run.
- Creation of `reference.*` where the pipeline does not produce it (Allegro: FFmpeg-decode the input stream, PC side): who emits the step and when it runs.
- Min-frame PSNR with `inf -> 1000`, matching both legacy decode/analyze scripts: what media-check psnr must report, and what changes in media-check if it reports differently.
- Raw-vs-encoded pairings between `output.*` and `reference.*` (raw metadata itself is ticket 06).
- `$WORK_DIR` path handling inside emitted commands and descriptors.

Deliverable: the psnr preparation + comparison recipe the check generator emits, plus the rule for when cropping applies.
