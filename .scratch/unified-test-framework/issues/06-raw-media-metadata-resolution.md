# Raw media metadata resolution

Type: grilling
Status: open
Blocked by: 01

## Question

How does the check learn width, height, and format of raw `output.*` / `reference.*` files when it builds the media-check descriptor?

Context from "Unified Excel schema": the schema has no Input/Output columns. The framework searches the case directory for `output.*` and `reference.*` before checking metrics. Raw files (.yuv, .raw) need width/height/format before media-check can read or compare them, and those values are not always present in the Pipeline string.

The ticket must decide:

- The source of the metadata and its precedence: pipeline caps (videoparse / video/x-raw), probing the counterpart file (media-check or ffprobe on the encoded side), declaration in the row, or a mix.
- The Allegro case: raw output decoded with no caps; legacy analyze.sh probed the encoded stream (ffprobe) and assumed NV12.
- The Encode v4l2 case: raw reference captured from v4l2src (caps exist in the pipeline).
- The default/assumed format when nothing declares one (omx decode output without caps).
- Who runs the probe and when (check time, PC side), and the error behavior when metadata cannot be resolved.

Deliverable: the metadata resolution recipe the check generator follows, with precedence rules and failure behavior. Prototyping with media-check / ffprobe is allowed.
