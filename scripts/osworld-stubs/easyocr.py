"""easyocr 的桩。

为什么要桩而不是装真的：easyocr 会拖进 torch（约 2GB），而这台机器上
`/` 只剩不到 2GB。更关键的是**它根本不该挡住这么多题**——
`desktop_env/evaluators/metrics/docs.py` 在模块顶层 import easyocr，
于是它定义的 33 个函数、36 道题全都被这一个 import 拦住，
而其中真正调用 OCR 的只有一个函数。

所以这里只提供一个会在**真正使用时**才报错的 Reader。
真需要 OCR 的题会拿到一句清楚的报错并被记成"环境不支持"，
而不是静默地判 0 分——把环境缺陷记成模型失败，是最坏的一种数据污染。
"""


class Reader:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "easyocr is stubbed out in this environment (it pulls ~2GB of torch and "
            "the disk has under 2GB free). This task genuinely needs OCR — record it "
            "as environment-unsupported, NOT as a model failure."
        )
