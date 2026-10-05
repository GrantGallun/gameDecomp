"""Fire variants for H2 (-O1 register local) and the early-return shape on the -O1 libultra residuals."""
import json
from pathlib import Path

V = {
    "__osAiDeviceBusy": {
        "register_local": [["    if (AI_STATUS_REG & 0x80000000) {\n        return 1;\n    }\n    return 0;\n",
                            "    register s32 status = AI_STATUS_REG;\n    if (status & 0x80000000) {\n        return 1;\n    } else {\n        return 0;\n    }\n"]],
        "plain_local": [["    if (AI_STATUS_REG & 0x80000000) {\n        return 1;\n    }\n    return 0;\n",
                         "    s32 status = AI_STATUS_REG;\n    if (status & 0x80000000) {\n        return 1;\n    }\n    return 0;\n"]],
    },
    "__osSpSetPc": {
        "register_local": [["    if (!(SP_STATUS_REG & 1)) {", "    register s32 status = SP_STATUS_REG;\n    if (!(status & 1)) {"]],
    },
    "osCartRomInit": {
        "early_return": [["    if (CartRomHandle.baseAddress == 0xB0000000) {\n\n    } else {",
                          "    if (CartRomHandle.baseAddress == 0xB0000000) {\n        return &CartRomHandle;\n    } else {"]],
    },
}
for f, v in V.items():
    Path(__file__).with_name(f"fire_{f}.json").write_text(json.dumps(v, indent=1))
