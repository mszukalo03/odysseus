import logging
from xml.etree import ElementTree as ET
from typing import Optional

logger = logging.getLogger(__name__)

OPML_NS = "http://opml.org/spec2"


def parse_opml(opml_content: str) -> list[dict]:
    feeds = []
    try:
        root = ET.fromstring(opml_content)
        body = root.find("body")
        if body is None:
            return feeds

        def _recurse(parent, group_path=None):
            if group_path is None:
                group_path = []
            for outline in parent.findall("outline"):
                attrs = outline.attrib
                feed_url = attrs.get("xmlUrl") or attrs.get("url", "")
                if feed_url:
                    feeds.append({
                        "feed_url": feed_url,
                        "title": attrs.get("text") or attrs.get("title", ""),
                        "site_url": attrs.get("htmlUrl") or attrs.get("siteUrl", ""),
                        "group_path": group_path,
                    })
                else:
                    child_name = attrs.get("text") or attrs.get("title", "")
                    _recurse(outline, group_path + [child_name])

        _recurse(body)
    except ET.ParseError as e:
        logger.warning("OPML parse error: %s", e)
    return feeds


def generate_opml(feeds: list[dict]) -> str:
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    title = ET.SubElement(head, "title")
    title.text = "Odysseus Feeds"
    body = ET.SubElement(root, "body")

    def _build_tree(feed_list):
        tree = {}
        for f in feed_list:
            path = f.get("group_path") or (f.get("group", "").split("/") if f.get("group") else [])
            if not path:
                tree.setdefault("__leaf__", []).append(f)
            else:
                first = path[0]
                rest = [{**f, "group_path": path[1:]}] if len(path) > 1 else [{**f, "group_path": []}]
                tree.setdefault(first, []).extend(rest)
        return tree

    tree = _build_tree(feeds)

    def _render(parent_el, subtree):
        for key, items in sorted(subtree.items()):
            if key == "__leaf__":
                for f in items:
                    ET.SubElement(parent_el, "outline", type="rss",
                                  text=f.get("title", ""),
                                  title=f.get("title", ""),
                                  xmlUrl=f.get("feed_url", ""),
                                  htmlUrl=f.get("site_url", ""))
            else:
                grp_el = ET.SubElement(parent_el, "outline", text=key, title=key)
                nested = {}
                for item in items:
                    p = item.get("group_path") or []
                    if not p:
                        nested.setdefault("__leaf__", []).append(item)
                    else:
                        nested.setdefault(p[0], []).append(
                            {**item, "group_path": p[1:]}
                        )
                _render(grp_el, nested)

    _render(body, tree)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
