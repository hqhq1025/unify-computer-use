"""acoustid 的桩（pyacoustid 需要系统库 libchromaprint，这台机器上装不上）。

和 easyocr 的桩同理：模块顶层的一个 import 会挡住整个模块里所有函数。
真正用到音频指纹的题会在**调用时**拿到明确报错，记成"环境不支持"，
不记成模型失败。
"""


def _unavailable(*args, **kwargs):
    raise RuntimeError(
        "acoustid is stubbed out here (pyacoustid needs the libchromaprint system "
        "library, which is not installable in this environment). A task that reaches "
        "this needs audio fingerprinting — record it as environment-unsupported, "
        "NOT as a model failure."
    )


match = fingerprint_file = compare_fingerprints = _unavailable


class FingerprintGenerationError(Exception):
    pass


class WebServiceError(Exception):
    pass
