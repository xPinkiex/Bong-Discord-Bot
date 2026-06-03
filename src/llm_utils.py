def _extract_response_text(response) -> str:
    content = response.content
    if isinstance(content, list):
        return "".join(chunk.text if hasattr(chunk, "text") else str(chunk) for chunk in content)
    return str(content or "")