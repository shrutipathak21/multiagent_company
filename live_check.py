
import sys

from ai_company.llm_free import make_free_llm

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("groq", "ollama"):
        print(__doc__)
        sys.exit(1)

    provider = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Connecting to {provider} (model={model or 'default'})...")
    llm = make_free_llm(provider, model)

    reply = llm.complete(
        system_prompt="You are a helpful assistant. Reply in exactly one short sentence.",
        user_prompt="Say hello and name the model you are.",
    )
    print("\n--- RAW RESPONSE ---")
    print(reply)
    print("--- END ---\n")
    print(f"{provider} is working. You can now run webapp.py and select "
          f"'{provider}' as the provider.")

if __name__ == "__main__":
    main()
