"""Resumable single-writer stages over existing atomic, content-addressed storage."""
from pathlib import Path

from ..rollout.artifacts import LocalObjects
from ..serialization import digest
from ..storage import read, save
from .contracts import validate


class Stages:
    """A local sequential pipeline, not a replacement for rollout scheduling.

    A failed or interrupted invocation is retried. Completed artifacts are
    verified before reuse; corruption is surfaced rather than overwritten.
    """

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.objects = LocalObjects(self.directory / "objects")

    def run(self, stage, version, inputs, config, seed, produce):
        request = {"stage": stage, "implementation_version": version,
                   "input_digests": sorted(set(inputs)), "config": config, "seed": seed}
        key = digest(request)
        path = self.directory / "stages" / (key + ".json")
        manifest = {"schema_version": "experience.stage.v1", "key": key, **request,
                    "state": "running", "attempt": 1}
        validate(manifest)
        # Verify every dependency, including on the completed fast path.
        for dependency in request["input_digests"]:
            self.objects.get(dependency)
        if path.exists():
            prior = validate(read(path), "experience-stage")
            if any(prior[field] != manifest[field] for field in ("key", *request)):
                raise ValueError("Stage manifest does not match its request")
            if prior["state"] == "completed":
                value = validate(self.objects.get(prior["output_digest"]))
                return {"key": key, "manifest": str(path), "output_digest": prior["output_digest"],
                        "reused": True, "value": value}
            manifest["attempt"] = prior["attempt"] + 1
        save(path, manifest)
        try:
            value = validate(produce())
            output = self.objects.put(value)
            save(path, validate({**manifest, "state": "completed", "output_digest": output}))
        except Exception as exc:
            save(path, validate({**manifest, "state": "failed", "error": type(exc).__name__ + ": " + str(exc)}))
            raise
        return {"key": key, "manifest": str(path), "output_digest": output, "reused": False, "value": value}
