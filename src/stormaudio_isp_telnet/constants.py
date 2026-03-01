from enum import Enum


class PowerCommand(Enum):
    ON = 1
    OFF = 2


class ProcessorState(Enum):
    ON = 1
    INITIALIZING = 2
    SHUTTING_DOWN = 3
    OFF = 4


class VideoInputID(Enum):
    NONE = 0
    HDMI1 = 1
    HDMI2 = 2
    HDMI3 = 3
    HDMI4 = 4
    HDMI5 = 5
    HDMI6 = 6
    HDMI7 = 7
    HDMI8 = 8


class AudioInputID(Enum):
    NONE = 0
    HDMI1 = 1
    HDMI2 = 2
    HDMI3 = 3
    HDMI4 = 4
    HDMI5 = 5
    HDMI6 = 6
    HDMI7 = 7
    HDMI8 = 8
    COAX_4 = 9
    COAX_5 = 10
    COAX_6 = 11
    UNUSED_12 = 12
    OPTICAL_1 = 13
    OPTICAL_2 = 14
    OPTICAL_3 = 15
    _16CH_AES = 16
    ROON_READY = 17
    STEREO_1_RCA = 18
    STEREO_2_RCA = 19
    STEREO_3_RCA = 20
    STEREO_4_RCA = 21
    STEREO_7_PLUS_1_RCA = 22
    ARC_EARC = 23
    STEREO_5_PLUS_1_RCA = 24
    STEREO_XLR_IN = 25
    _32CH_AES67 = 26


class AudioZone2InputID(Enum):
    NONE = 0
    HDMI1 = 1
    HDMI2 = 2
    HDMI3 = 3
    HDMI4 = 4
    HDMI5 = 5
    HDMI6 = 6
    HDMI7 = 7
    UNUSED_8 = 8
    COAX_4 = 9
    COAX_5 = 10
    COAX_6 = 11
    UNUSED_12 = 12
    OPTICAL_1 = 13
    OPTICAL_2 = 14
    OPTICAL_3 = 15
    UNUSED_16 = 16
    UNUSED_17 = 17
    STEREO_1_RCA = 18
    STEREO_2_RCA = 19
    STEREO_3_RCA = 20
    STEREO_4_RCA = 21
    UNUSED_22 = 22
    UNUSED_23 = 23
    UNUSED_24 = 24
    STEREO_XLR_IN = 25
    UNUSED_26 = 26
    ARC_2 = 27


class ZoneLayoutType(Enum):
    DOWNMIX = 2000
    MONO = 2001
    STEREO_AND_STEREO_AV = 2002
    HEADPHONE = 2003


class ZoneType(Enum):
    MAIN_SPEAKERS = 0
    ALTERNATE_SPEAKERS = 1


class LoudnessLevel(Enum):
    OFF = 0
    LOW = 1
    MEDIUM = 2
    FULL = 3


class SurroundModeID(Enum):
    NATIVE = 0
    STEREO_DOWNMIX = 1
    DOLBY_SURROUND = 2
    DTS_NEURAL_X = 3
    AURO_MATIC = 4


class DolbyModeID(Enum):
    OFF = 0
    MOVIE = 1
    MUSIC = 2
    NIGHT = 3
