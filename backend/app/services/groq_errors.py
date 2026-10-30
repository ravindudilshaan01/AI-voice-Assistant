from groq import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError


class ServiceError(Exception):
    pass


def friendly_error(error: Exception, operation: str) -> ServiceError:
    if isinstance(error, RateLimitError):
        return ServiceError("Groq is busy or your rate limit was reached. Please try again shortly.")
    if isinstance(error, APITimeoutError):
        return ServiceError(f"{operation} timed out. Please try again.")
    if isinstance(error, APIConnectionError):
        return ServiceError("Could not reach Groq. Check your internet connection and try again.")
    if isinstance(error, APIStatusError):
        if error.status_code in (401, 403):
            return ServiceError("Groq authentication failed. Check GROQ_API_KEY in backend/.env.")
        if error.status_code == 413:
            return ServiceError("The recording is too large for Groq.")
        return ServiceError(f"{operation} is unavailable right now. Please try again.")
    return ServiceError(f"{operation} failed. Please try again.")
