"""项目内可识别的错误类型。"""


class AutocutError(RuntimeError):
    """可向用户直接解释的业务错误。"""


class ConfigurationError(AutocutError):
    """配置缺失或不合法。"""


class ExternalServiceError(AutocutError):
    """外部模型或语音服务调用失败。"""


class DraftCompatibilityError(AutocutError):
    """当前剪映版本尚未通过草稿兼容性验证。"""
