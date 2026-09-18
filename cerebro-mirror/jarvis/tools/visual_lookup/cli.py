import urllib.error

import click

from visual_lookup.read import parse_vision_response, query_vision, read_image, vision_url
from visual_lookup.resolve import format_answer, resolve_candidate


def _render(answer: dict) -> list[str]:
    """Candidates first, each with the picture that lets a human confirm the guess."""
    lines: list[str] = []
    candidates = answer.get("candidates") or []
    sources = answer.get("sources") or []

    if candidates:
        best, rest = candidates[0], candidates[1:]
        lines.append(best["title"])
        if best.get("thumbnail"):
            lines.append(f"  image: {best['thumbnail']}")
        lines.append(f"  {best['url']}")
        if best.get("extract"):
            lines.append(f"  {best['extract']}")
        if rest:
            lines.append("")
            lines.append("Other candidates:")
            lines.extend(f"  - {c['title']}  {c['url']}" for c in rest)
    else:
        lines.append("No encyclopedia match.")
        if sources:
            lines.append("Images of it:")
            lines.extend(f"  - {s['title']}  {s['url']}" for s in sources)
        else:
            markings = answer.get("markings") or []
            lines.append(f"  markings: {', '.join(markings) if markings else '(none)'}")

    if answer.get("unavailable"):
        lines.append(f"(could not reach: {', '.join(answer['unavailable'])})")
    return lines


@click.command()
@click.argument('image_path')
@click.argument('question')
def main(image_path, question):
    read_image(image_path)  # fail early, and with FileNotFoundError, if it is unreadable
    try:
        vision_response = query_vision(image_path, question)
    except (urllib.error.URLError, OSError) as exc:
        raise click.ClickException(f"vision service unreachable at {vision_url()}: {exc}")

    answer = format_answer(resolve_candidate(parse_vision_response(vision_response)))
    for line in _render(answer):
        click.echo(line)


if __name__ == '__main__':
    main()
