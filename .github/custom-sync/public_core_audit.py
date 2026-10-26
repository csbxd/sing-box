#!/usr/bin/env python3
"""Read-only public core release byte verification. Never executes assets."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

REPO = "csbxd/sing-box"

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def api(path):
    return json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{REPO}/{path}"], text=True))

def snapshot(release):
    return {
        "id": release["id"],
        "tag_name": release["tag_name"],
        "draft": release["draft"],
        "prerelease": release["prerelease"],
        "body": release["body"],
        "assets": sorted(
            [(a["id"], a["name"], a["size"], a.get("digest"),
              a["browser_download_url"], a["updated_at"])
             for a in release["assets"]])
    }

def main():
    require(os.environ["GITHUB_REPOSITORY"] == REPO, "Wrong repository")
    request = json.loads(Path(
        ".github/custom-sync/public-core-audit-request.json").read_text())
    require(set(request) == {
        "schema", "request_id", "tag", "source_sha",
        "source_tree", "source_fingerprint"}, "Unexpected request fields")
    require(request["schema"] == 1, "Wrong schema")
    require(re.fullmatch(r"[A-Za-z0-9._-]{1,100}", request["request_id"]),
            "Invalid request ID")
    tag = request["tag"]
    require(re.fullmatch(r"v\d+\.\d+\.\d+(?:-[a-z]+\.\d+\.c\d+|-c\d+)", tag),
            "Invalid tag")
    for field in ("source_sha", "source_tree"):
        require(re.fullmatch(r"[0-9a-f]{40}", request[field]), field)
    require(re.fullmatch(r"[0-9a-f]{64}", request["source_fingerprint"]),
            "Invalid fingerprint")

    release = api(f"releases/tags/{tag}")
    require(not release["draft"] and release["tag_name"] == tag,
            "Release is not public")
    ref = api(f"git/ref/tags/{tag}")["object"]
    require(ref["type"] == "commit" and ref["sha"] == request["source_sha"],
            "Tag does not identify exact source")
    commit = api(f"git/commits/{request['source_sha']}")
    require(commit["tree"]["sha"] == request["source_tree"], "Wrong source tree")

    version = tag[1:]
    archives = {
        f"sing-box-{version}-{system}-{arch}."
        + ("zip" if system == "windows" else "tar.gz")
        for system in ("linux", "windows", "darwin")
        for arch in ("amd64", "arm64")
    }
    expected = archives | {"SHA256SUMS", "source-metadata.json"}
    assets = release["assets"]
    require(len(assets) == 8 and {a["name"] for a in assets} == expected,
            "Unexpected or duplicate asset set")
    digests = {}
    texts = {}
    for asset in assets:
        name = asset["name"]
        url = f"https://github.com/{REPO}/releases/download/{tag}/{name}"
        require(asset["browser_download_url"] == url, "Unexpected asset URL")
        require(asset["state"] == "uploaded", "Asset incomplete")
        limit = 128 * 1024 * 1024 if name in archives else 1024 * 1024
        require(0 < asset["size"] <= limit, "Asset size outside bound")
        digest = hashlib.sha256()
        size = 0
        data = bytearray()
        # No Authorization header is sent to download URLs or redirect targets.
        with urllib.request.urlopen(url, timeout=60) as response:
            require(response.status == 200, "Download failed")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                require(size <= limit, "Download exceeded bound")
                digest.update(chunk)
                if name not in archives:
                    data.extend(chunk)
        value = digest.hexdigest()
        require(size == asset["size"], "Downloaded size mismatch")
        require(asset.get("digest") == "sha256:" + value,
                "Public bytes disagree with GitHub digest")
        digests[name] = value
        if name not in archives:
            texts[name] = data.decode("utf-8")

    sums = {}
    for line in texts["SHA256SUMS"].splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        require(match is not None, "Invalid checksum line")
        value, name = match.groups()
        require(name not in sums, "Duplicate checksum")
        sums[name] = value
    require(set(sums) == archives, "Checksum manifest asset set differs")
    require(all(sums[name] == digests[name] for name in archives),
            "Checksum manifest does not match public bytes")

    metadata = json.loads(texts["source-metadata.json"])
    markers = re.findall(
        r"<!-- custom-core-release: (\{[^\n]+\}) -->", release["body"])
    require(len(markers) == 1, "Missing or ambiguous metadata marker")
    require(metadata == json.loads(markers[0]), "Metadata file/marker mismatch")
    for field in ("source_sha", "source_tree", "source_fingerprint", "tag"):
        require(metadata[field] == request[field], "Metadata mismatch: " + field)
    require(metadata["version"] == version and metadata["skip"] is False,
            "Invalid version metadata")
    require(metadata["prerelease"] == release["prerelease"],
            "Prerelease metadata mismatch")

    require(snapshot(api(f"releases/tags/{tag}")) == snapshot(release),
            "Release changed during verification")
    require(api(f"git/ref/tags/{tag}")["object"] == ref,
            "Tag changed during verification")
    result = {
        "schema": 1,
        "request_id": request["request_id"],
        "control_commit": os.environ["GITHUB_SHA"],
        "release_url": release["html_url"],
        "source_sha": request["source_sha"],
        "source_tree": request["source_tree"],
        "asset_sha256": digests,
        "status": "verified-public-bytes"
    }
    output = json.dumps(result, indent=2) + "\n"
    Path("public-core-audit-result.json").write_text(output)
    print(output)

if __name__ == "__main__":
    main()
