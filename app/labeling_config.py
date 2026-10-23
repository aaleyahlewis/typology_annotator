"""Read config/labeling_config.xml so validation and export follow the XML, not hard-coded names."""
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache

from .settings import settings


@dataclass(frozen=True)
class LabelingConfig:
    xml: str
    sha256: str
    object_name: str            # <Paragraphs name=...>
    value_key: str              # data key, e.g. "dialogue" from value="$dialogue"
    name_key: str
    text_key: str
    span_control: str           # <ParagraphLabels name=...>
    user_label: str             # first <Label> = participant's own text
    assistant_label: str        # second <Label> = AI text
    controls: dict              # control name -> {"type", "per_region", "choices", "multiple", "max_submissions", "label"}


@lru_cache
def load(path=None):
    path = path or settings.labeling_config_path
    xml = open(path, encoding="utf-8").read()
    root = ET.fromstring(xml)
    paragraphs = root.find(".//Paragraphs")
    labels = root.find(".//ParagraphLabels")
    if paragraphs is None or labels is None:
        raise ValueError("labeling config needs <Paragraphs> and <ParagraphLabels>")
    label_values = [l.get("value") for l in labels.findall("Label")]
    if len(label_values) != 2:
        raise ValueError("<ParagraphLabels> must have exactly two labels: the participant's text, then the AI's text")
    controls = {labels.get("name"): {"type": "paragraphlabels", "per_region": True, "choices": set(label_values), "label": None}}
    # Which label's panel each control sits in (via <View whenLabelValue=...>).
    label_of = {}

    def walk(el, label):
        label = el.get("whenLabelValue", label) if el.tag == "View" else label
        if el.tag in ("Choices", "TextArea"):
            label_of[el.get("name")] = label
        for child in el:
            walk(child, label)

    walk(root, None)
    for tag in ("Choices", "TextArea"):
        for el in root.iter(tag):
            controls[el.get("name")] = {
                "label": label_of.get(el.get("name")),
                "type": tag.lower(),
                "per_region": el.get("perRegion") == "true",
                "choices": {c.get("value") for c in el.findall("Choice")} if tag == "Choices" else None,
                "multiple": el.get("choice") == "multiple",
                "max_submissions": int(el.get("maxSubmissions") or 0) or None,
            }
    return LabelingConfig(
        xml=xml,
        sha256=hashlib.sha256(xml.encode()).hexdigest(),
        object_name=paragraphs.get("name"),
        value_key=paragraphs.get("value", "").lstrip("$"),
        name_key=paragraphs.get("nameKey", "author"),
        text_key=paragraphs.get("textKey", "text"),
        span_control=labels.get("name"),
        user_label=label_values[0],
        assistant_label=label_values[1],
        controls=controls,
    )
