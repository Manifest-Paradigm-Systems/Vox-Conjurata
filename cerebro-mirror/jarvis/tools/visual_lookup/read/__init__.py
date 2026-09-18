import base64
import json
import os
import re
import urllib.request

DEFAULT_VISION_URL = "http://127.0.0.1:8084"
VISION_TIMEOUT = float(os.environ.get("VISUAL_LOOKUP_VISION_TIMEOUT", "120"))


def vision_url() -> str:
    """Where the vision model lives.

    Read per call, not at import, so a test or a deployment can move it without
    reimporting the package.
    """
    return os.environ.get("VISUAL_LOOKUP_VISION_URL", DEFAULT_VISION_URL).rstrip("/")


def read_image(image_path: str) -> str:
    with open(image_path, 'rb') as image_file:
        return base64.b64encode(image_file.read()).decode()


def build_prompt(question: str = '') -> str:
    """Ask for what the resolver needs, in a shape the parser can read.

    Asking for "the shortest name" gets "Spider", and searching that returns Spider-Man
    and a disambiguation page before anything alive. Asking for the *most specific* name
    gets "cellar spider", which returns Pholcidae and Pholcus phalangioides immediately.
    The prompt is the difference between an answer and a wrong answer.
    """
    prompt = ("Look at the object in the image and identify it as precisely as you can. "
              "Name the kind or species when you can tell, not just the general category "
              '(for example "cellar spider", not "spider").\n'
              "Also read any visible markings and part numbers off it.\n"
              'Reply with only JSON in this shape: {"identification": "<the most specific '
              'name you can give>", "markings": ["<each marking>"], '
              '"description": "<one line, including colour, shape and size>"}\n'
              "Use an empty list for markings when there are none — do not describe the "
              "absence of markings in that field.")
    if question:
        prompt += f" {question}"
    return prompt


def _as_dict(text: str) -> dict | None:
    """The reply as a JSON object, bare or wrapped in a sentence.

    Asked for "only JSON" the model still answers "According to the image, the answer is
    {...}." — so look inside prose rather than making the prompt do all the work.
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = text.find('{')
    while start != -1:
        end = text.rfind('}')
        if end > start:
            try:
                data = json.loads(text[start:end + 1])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        start = text.find('{', start + 1)
    return None


# A part number is short. Anything longer is the model answering the question instead
# of the field — it likes to put "no visible markings or part numbers" in `markings`.
MAX_MARKING = 40
_ABSENT = re.compile(r"^(none|no visible|no distinct|no markings|not visible|n/?a)\b",
                     re.IGNORECASE)


def _is_real_marking(value: str) -> bool:
    return bool(value) and len(value) <= MAX_MARKING and not _ABSENT.match(value)


def _shape(data: dict) -> dict | None:
    """The fields we ask for, from a reply that may carry only some of them."""
    if not isinstance(data, dict):
        return None
    markings = data.get("markings", [])
    if not isinstance(markings, list):
        return None
    if not any(key in data for key in ("identification", "markings", "description")):
        return None
    return {
        "identification": str(data.get("identification") or "").strip(),
        "markings": [str(marking).strip() for marking in markings
                     if _is_real_marking(str(marking).strip())],
        "description": str(data.get("description") or "").strip(),
    }


def parse_vision_response(text: str) -> dict:
    """Pull what the model saw out of its reply, in whatever shape it arrived.

    Takes the model's *text*. Being handed an already-parsed dict is a programming
    error rather than a case to absorb — absorbing it is what let the CLI hand a dict
    to a JSON parser and pass its component tests anyway.
    """
    if not isinstance(text, str):
        raise TypeError(
            f"parse_vision_response expects the model's reply text, "
            f"got {type(text).__name__}")

    shaped = _shape(_as_dict(text) or {})
    if shaped is not None:
        return shaped

    if 'markings:' in text:
        parts = text.split('markings:', 1)
        markings = [part.strip() for part in parts[1].split(',') if part.strip()]
        return {"identification": "", "markings": markings,
                "description": parts[0].strip()}

    # Nothing structured came back. The model still looked at the image, so keep what it
    # said as the description rather than dropping the only signal we have — a prose
    # reply used to vanish whole, leaving the resolver nothing to work from.
    return {"identification": "", "markings": [], "description": text.strip()}


def query_vision(image_path: str, question: str = '') -> str:
    """Ask the vision model about an image and return its reply as raw text.

    The reply is text, not a parsed dict: parsing is parse_vision_response's job, and
    having both of them do it is how the CLI ended up handing a dict to a JSON parser.

    Failure raises. A vision service that is down must never be indistinguishable from
    an object that carries no markings.
    """
    image_data = read_image(image_path)
    prompt = build_prompt(question)
    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
            ],
        }],
        "max_tokens": 512,
    }
    request = urllib.request.Request(
        f"{vision_url()}/v1/chat/completions",
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=VISION_TIMEOUT) as response:
        body = json.loads(response.read().decode('utf-8'))

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            f"vision service replied without a message: {body!r}") from exc
