
import sys

from ai_company.conflict import FileStore
from ai_company.sandbox_exec import RemoteWorkspace, SandboxUnavailable

def main():
    print("Building a tiny test file store...")
    fs = FileStore()
    fs.commit("hello.py", "print('sandbox is alive')\n", base_version=0, author="check")

    print("Connecting to E2B and pushing files...")
    remote = RemoteWorkspace(fs)
    try:
        remote.push_files()
        print("Running a command inside the sandbox...")
        exit_code, output = remote.run("python3 hello.py")
    except SandboxUnavailable as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)
    finally:
        remote.close()

    print("\n--- SANDBOX OUTPUT ---")
    print(output)
    print(f"--- exit code: {exit_code} ---\n")

    if exit_code == 0 and "sandbox is alive" in output:
        print("E2B is working. webapp_cloud.py can safely execute user code.")
    else:
        print("Sandbox ran but output looked unexpected — investigate before deploying.")

if __name__ == "__main__":
    main()
