class TranslationError(Exception):
    """Base for all translator errors."""


class UnsupportedFormatError(TranslationError):
    pass


class PasswordProtectedError(TranslationError):
    pass


class MalformedDocError(TranslationError):
    pass


class LLMError(TranslationError):
    pass


class TokenLimitError(LLMError):
    pass
