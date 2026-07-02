import os
import shlex
from dataclasses import dataclass


###############################################################################
#                              GLOBAL VARIABLES                                #
###############################################################################

RESULT_FILE_NAME = "result.txt"

RAW_VIDEO_EXTENSIONS     = (".raw", ".yuv")
ENCODED_VIDEO_EXTENSIONS = (".264", ".265")
VIDEO_EXTENSIONS         = ENCODED_VIDEO_EXTENSIONS

PSNR_INPUT_COLUMN = "Input"
INFINITE_PSNR_VALUE = 1000

# GStreamer format -> FFmpeg pixel format. Add new supported raw formats here.
GSTREAMER_TO_FFMPEG_FORMAT = {
    "NV12"  : "nv12",
    "YUY2"  : "yuyv422",
    "RGB16" : "rgb565le",
    "RGB"   : "rgb24",
    "RGBA"  : "rgba",
    "GRAY8" : "gray",
}


###############################################################################
#                               MEDIA SPECIFICATION                           #
###############################################################################

@dataclass(frozen = True)
class MediaSpec:
    """
    One Input or Output cell used by file-comparison metrics.
    """

    path                : str
    extension           : str
    width               : int | None = None
    height              : int | None = None
    ffmpeg_pixel_format : str | None = None

    @property
    def is_raw(self) -> bool:
        """
        Return True when this media file must be opened as raw video.
        """
        return self.extension in RAW_VIDEO_EXTENSIONS

    def render_ffmpeg_input(self) -> str:
        """
        Render this media file as FFmpeg input arguments.
        """
        quoted_path = quote_expandable_path(self.path)

        if not self.is_raw:
            return f"-i {quoted_path}"

        return (
            f"-f rawvideo "
            f"-pixel_format {shlex.quote(self.ffmpeg_pixel_format)} "
            f"-video_size {self.width}x{self.height} "
            f"-i {quoted_path}"
        )


def quote_expandable_path(path: str) -> str:
    """
    Quote a Bash path while allowing only WORK_DIR environment expansion.

    The specification uses $WORK_DIR paths. shlex.quote() would preserve the
    dollar sign literally, so this helper protects the supported placeholders,
    escapes other shell-expansion characters, then restores the placeholders.
    """
    placeholders = {
        "${WORK_DIR}" : "__WORK_DIR_BRACED__",
        "$WORK_DIR"   : "__WORK_DIR__",
    }

    for variable, marker in placeholders.items():
        path = path.replace(variable, marker)

    path = (
        path.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )

    for variable, marker in placeholders.items():
        path = path.replace(marker, variable)

    return f'"{path}"'


def parse_media_spec(cell_text: str, cell_name: str) -> MediaSpec:
    """
    Parse a PSNR Input or Output cell into a validated media specification.

    Encoded files may provide only a path or all four fields. Raw files always
    require path, width, height, and GStreamer format.
    """
    fields = [field.strip() for field in str(cell_text).split("|")]

    if len(fields) not in (1, 4):
        raise ValueError(
            f"{cell_name} must be either '<path>' or "
            f"'<path>|<width>|<height>|<format>'"
        )

    path = fields[0]

    if not path:
        raise ValueError(f"{cell_name} path is empty")

    extension = os.path.splitext(path)[1].lower()

    if extension in ENCODED_VIDEO_EXTENSIONS:
        return MediaSpec(
            path      = path,
            extension = extension,
        )

    if extension not in RAW_VIDEO_EXTENSIONS:
        extension_text = extension or "<none>"

        raise ValueError(
            f"{cell_name} has unsupported extension '{extension_text}'"
        )

    if len(fields) != 4:
        raise ValueError(
            f"Raw {cell_name} must define path, width, height, and format"
        )

    width_text, height_text, gst_format = fields[1:]

    try:
        width  = int(width_text)
        height = int(height_text)
    except ValueError:
        raise ValueError(f"Raw {cell_name} width and height must be integers")

    if width <= 0 or height <= 0:
        raise ValueError(f"Raw {cell_name} width and height must be positive")

    ffmpeg_pixel_format = GSTREAMER_TO_FFMPEG_FORMAT.get(gst_format.upper())

    if not ffmpeg_pixel_format:
        raise ValueError(f"Raw {cell_name} has unsupported format '{gst_format}'")

    return MediaSpec(
        path                = path,
        extension           = extension,
        width               = width,
        height              = height,
        ffmpeg_pixel_format = ffmpeg_pixel_format,
    )


###############################################################################
#                                 BASE METRIC                                 #
###############################################################################

class Metric:
    """
    Base class for metric handlers.

    A metric handler emits the bash snippet that sets a shell variable named
    ``value`` with the metric extracted from a test case's output/result
    files. Subclasses override :meth:`extract` to provide the extraction
    command while the base class provides shared helpers such as output file
    extension/existence validation.
    """

    # Required output file extensions. An empty list means no validation.
    required_extensions = []
    required_row_fields = ()

    def __init__(self, row_values = None, output = ""):
        """
        Initialize a metric with its declared specification-row values.

        Most metrics use only output. Metrics that need additional columns can
        declare required_row_fields and consume row_values in a subclass.
        """
        self.row_values = row_values or {}
        self.output     = output

    def extract(self, output: str, result_file: str) -> str:
        """
        Return the bash command that sets ``value`` from the given files.

        Subclasses must override this method.
        """
        raise NotImplementedError

    def render(self, output: str, result_file: str) -> str:
        """
        Render the full bash snippet for this metric.

        When the metric requires a specific output extension, the extraction
        command is guarded so that a missing file or a wrong extension marks
        the check as failed by leaving ``value`` empty.
        """
        extract_cmd = self.extract(
            output      = output,
            result_file = result_file,
        )

        if not self.required_extensions:
            return extract_cmd

        return self._guard_output(
            output      = output,
            extract_cmd = extract_cmd,
        )

    def _guard_output(self, output: str, extract_cmd: str) -> str:
        """
        Wrap ``extract_cmd`` with extension and existence validation.
        """
        quoted_output = quote_expandable_path(output)

        extension = os.path.splitext(output)[1].lower()
        valid_ext = extension in self.required_extensions

        if not valid_ext:
            return f'value=""'

        return (
            f'if [[ -f {quoted_output} ]]; then\n'
            f'    {extract_cmd}\n'
            f'else\n'
            f'    value=""\n'
            f'fi'
        )


###############################################################################
#                               RETURN METRIC                                 #
###############################################################################

class ReturnMetric(Metric):
    """
    Read the recorded pipeline return code from the result file.
    """

    def extract(self, output: str, result_file: str) -> str:
        path = result_file or RESULT_FILE_NAME

        return f"value=$(cat {shlex.quote(path)})"


###############################################################################
#                                WIDTH METRIC                                 #
###############################################################################

class WidthMetric(Metric):
    """
    Extract the encoded stream width using ffprobe.
    """

    required_extensions = VIDEO_EXTENSIONS

    def extract(self, output: str, result_file: str) -> str:
        quoted_output = quote_expandable_path(output)

        return (
            f"value=$(ffprobe -v error -select_streams v:0 "
            f"-show_entries stream=width "
            f"-of default=noprint_wrappers=1:nokey=1 {quoted_output})"
        )


###############################################################################
#                               HEIGHT METRIC                                 #
###############################################################################

class HeightMetric(Metric):
    """
    Extract the encoded stream height using ffprobe.
    """

    required_extensions = VIDEO_EXTENSIONS

    def extract(self, output: str, result_file: str) -> str:
        quoted_output = quote_expandable_path(output)

        return (
            f"value=$(ffprobe -v error -select_streams v:0 "
            f"-show_entries stream=height "
            f"-of default=noprint_wrappers=1:nokey=1 {quoted_output})"
        )


###############################################################################
#                              FRAMERATE METRIC                               #
###############################################################################

class FramerateMetric(Metric):
    """
    Extract the encoded stream frame rate and normalize it to integer fps.

    ffprobe reports ``r_frame_rate`` as a rational (e.g. ``24/1``), so the
    value is normalized to an integer with awk.
    """

    required_extensions = VIDEO_EXTENSIONS

    def extract(self, output: str, result_file: str) -> str:
        quoted_output = quote_expandable_path(output)

        return (
            f"value=$(ffprobe -v error -select_streams v:0 "
            f"-show_entries stream=r_frame_rate "
            f"-of default=noprint_wrappers=1:nokey=1 {quoted_output} "
            f"| awk -F'/' '{{printf \"%d\", $1/$2}}')"
        )


###############################################################################
#                                LEVEL METRIC                                 #
###############################################################################

class LevelMetric(Metric):
    """
    Extract the encoded stream level using ffprobe (string value).
    """

    required_extensions = VIDEO_EXTENSIONS

    def extract(self, output: str, result_file: str) -> str:
        quoted_output = quote_expandable_path(output)

        return (
            f"value=$(ffprobe -v error -select_streams v:0 "
            f"-show_entries stream=level "
            f"-of default=noprint_wrappers=1:nokey=1 {quoted_output})"
        )


###############################################################################
#                               PROFILE METRIC                                #
###############################################################################

class ProfileMetric(Metric):
    """
    Extract the encoded stream profile using ffprobe (string value).
    """

    required_extensions = VIDEO_EXTENSIONS

    def extract(self, output: str, result_file: str) -> str:
        quoted_output = quote_expandable_path(output)

        return (
            f"value=$(ffprobe -v error -select_streams v:0 "
            f"-show_entries stream=profile "
            f"-of default=noprint_wrappers=1:nokey=1 {quoted_output})"
        )


###############################################################################
#                                PSNR METRIC                                  #
###############################################################################

class PsnrMetric(Metric):
    """
    Calculate the minimum PSNR between Input and Output media files.
    """

    required_row_fields = (PSNR_INPUT_COLUMN,)

    def __init__(self, row_values = None, output = ""):
        super().__init__(row_values = row_values, output = output)

        self.input_spec  = parse_media_spec(
            self.row_values.get(PSNR_INPUT_COLUMN, ""),
            PSNR_INPUT_COLUMN,
        )
        self.output_spec = parse_media_spec(self.output, "Output")

    def extract(self, output: str, result_file: str) -> str:
        input_path  = quote_expandable_path(self.input_spec.path)
        output_path = quote_expandable_path(self.output_spec.path)

        return (
            f"if [[ -f {input_path} && -f {output_path} ]]; then\n"
            f"    psnr_output=$(ffmpeg {self.input_spec.render_ffmpeg_input()} "
            f"{self.output_spec.render_ffmpeg_input()} "
            f"-lavfi \"[0:v][1:v]psnr\" -an -f null - 2>&1)\n"
            f"    if [[ $? -eq 0 ]]; then\n"
            f"        value=$(printf '%s\\n' \"${{psnr_output}}\" "
            f"| sed -n 's/.*min:\\([^ ]*\\).*/\\1/p' | tail -n 1)\n"
            f"        if [[ \"${{value}}\" == \"inf\" ]]; then "
            f"value={INFINITE_PSNR_VALUE}; fi\n"
            f"    else\n"
            f"        value=\"\"\n"
            f"    fi\n"
            f"else\n"
            f"    value=\"\"\n"
            f"fi"
        )


###############################################################################
#                              METRIC REGISTRY                                #
###############################################################################

METRIC_HANDLERS = {
    "return"    : ReturnMetric,
    "width"     : WidthMetric,
    "height"    : HeightMetric,
    "framerate" : FramerateMetric,
    "level"     : LevelMetric,
    "profile"   : ProfileMetric,
    "psnr"      : PsnrMetric,
}
