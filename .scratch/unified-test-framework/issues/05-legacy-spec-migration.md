# Legacy spec migration mapping

Type: grilling
Status: open
Blocked by: 01

## Question

For each legacy program, decide the exact mapping from its current form into the unified schema:

- GST Decode: cases were generated from a platform config (bypass x standard x input x buffer mode x scale x format; see `Refer/GStreamer_Test/Decode/create_test.py` + `util.py`). Migration exports that product into explicit hand-written rows once.
- GST Encode: map the Video_h264 / Video_h265 / v4l2_h264 / v4l2_h265 / Cropping sheets (`Refer/GStreamer_Test/Encode/`), including the fate of the Autogen column.
- Allegro: the stream inventory (`Allegro_streams_h264/h265` suite directories) becomes hand-written rows; `list_unsupport_h264` / `list_unsupport_h265` entries become Skip values with reasons.

- Naming and hooks: legacy outputs (`omx.yuv`, `<stream>_target.yuv`, `<test_program>.<ext>`) become the `output.*` convention; the v4l2 raw-capture sink becomes `reference.yuv`; the Encode board pre-command `v4l2-init.sh` maps to the Prerun column.

Deliverable: three mapping tables (legacy field -> unified column) plus the conversion approach per program (one-shot script vs manual).