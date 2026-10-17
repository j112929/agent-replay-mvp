"""Trusted optional provider call. Never used by hosted simulation or offline CI."""
import json
import sys


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE,(65536,65536))
    resource.setrlimit(resource.RLIMIT_CPU,(30,30))
    resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536: return 2
        value=json.loads(raw)
        from ..providers.registry import complete
        result=complete(value['project'],value['provider_id'],value['request'])
        output=json.dumps(result)
        if len(output.encode())>65536: return 2
        print(output);return 0
    except Exception: return 2


if __name__=='__main__': sys.exit(main())
