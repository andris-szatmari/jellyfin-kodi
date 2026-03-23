#!/usr/bin/env python

import argparse
import os
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

import yaml

RUNTIME_TOP_LEVEL_FILES = (
    "addon.xml",
    "service.py",
    "default.py",
    "context.py",
    "context_play.py",
    "LICENSE.txt",
)
RUNTIME_TOP_LEVEL_DIRS = (
    "resources",
    "jellyfin_kodi",
    "typings",
)


def indent(elem: ET.Element, level: int = 0) -> None:
    """
    Nicely formats output xml with newlines and spaces
    https://stackoverflow.com/a/33956544
    """
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def create_addon_xml(config: dict, source: str, py_version: str) -> None:
    """
    Create addon.xml from template file
    """
    # Load template file
    with open("{}/.build/template.xml".format(source), "r") as f:
        tree = ET.parse(f)
        root = tree.getroot()

    # Populate dependencies in template
    dependencies = config["dependencies"].get(py_version)
    for dep in dependencies:
        ET.SubElement(root.find("requires"), "import", attrib=dep)

    # Populate version string
    addon_version = config.get("version")
    root.attrib["version"] = "{}+{}".format(addon_version, py_version)

    # Populate Changelog
    date = datetime.today().strftime("%Y-%m-%d")
    changelog = config.get("changelog")
    for section in root.findall("extension"):
        news = section.findall("news")
        if news:
            news[0].text = "v{} ({}):\n{}".format(addon_version, date, changelog)

    # Format xml tree
    indent(root)

    # Write addon.xml
    tree.write("{}/addon.xml".format(source), encoding="utf-8", xml_declaration=True)


def zip_files(py_version: str, source: str, target: str, dev: bool) -> None:
    """
    Create installable addon zip archive
    """
    archive_name = "plugin.video.jellyfin+{}.zip".format(py_version)
    archive_path = os.path.join(target, archive_name)

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for relative_path in iter_runtime_files(source, dev):
            z.write(
                os.path.join(source, relative_path),
                os.path.join("plugin.video.jellyfin", relative_path),
            )


def file_filter(file_name: str) -> bool:
    """
    True if file_name is meant to be included
    """
    return (
        not (
            file_name.startswith("plugin.video.jellyfin") and file_name.endswith(".zip")
        )
        and not file_name.endswith(".pyo")
        and not file_name.endswith(".pyc")
        and not file_name.endswith(".pyd")
        and file_name != ".DS_Store"
        and file_name != "AGENTS.md"
        and file_name != "release.yaml"
    )


def runtime_filter(path_name: str, source: str) -> bool:
    """
    True if path_name is part of the addon runtime payload.
    """
    relative_path = os.path.relpath(path_name, source)
    top_level = relative_path.split(os.path.sep, 1)[0]

    return top_level in RUNTIME_TOP_LEVEL_FILES or top_level in RUNTIME_TOP_LEVEL_DIRS


def folder_filter(folder_name: str, source: str) -> bool:
    """
    True if folder_name is meant to be included
    """
    if not runtime_filter(folder_name, source):
        return False

    filters = [
        ".ci",
        ".git",
        ".github",
        ".build",
        ".mypy_cache",
        ".pytest_cache",
        ".venv",
        ".vscode",
        "__pycache__",
        "downloads",
        "tests",
    ]
    for f in filters:
        if f in folder_name.split(os.path.sep):
            return False

    return True


def iter_runtime_files(source: str, dev: bool):
    """
    Yield addon runtime files relative to source.
    """
    for top_level_file in RUNTIME_TOP_LEVEL_FILES:
        if file_filter(top_level_file):
            yield top_level_file

    for top_level_dir in RUNTIME_TOP_LEVEL_DIRS:
        root_dir = os.path.join(source, top_level_dir)

        for root, dirs, files in os.walk(root_dir):
            if not dev:
                dirs[:] = [
                    directory
                    for directory in dirs
                    if folder_filter(os.path.join(root, directory), source)
                ]

            for filename in filter(file_filter, files):
                file_path = os.path.join(root, filename)

                if dev or folder_filter(file_path, source):
                    yield os.path.relpath(file_path, source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build flags:")
    parser.add_argument("--version", type=str, choices=("py2", "py3"), default="py3")

    parser.add_argument("--source", type=Path, default=Path(__file__).absolute().parent)

    parser.add_argument("--target", type=Path, default=Path(__file__).absolute().parent)

    parser.add_argument("--dev", dest="dev", action="store_true")
    parser.set_defaults(dev=False)

    args = parser.parse_args()

    # Load config file
    config_path = os.path.join(args.source, "release.yaml")
    with open(config_path, "r") as fh:
        release_config = yaml.safe_load(fh)

    create_addon_xml(release_config, args.source, args.version)

    zip_files(args.version, args.source, args.target, args.dev)
