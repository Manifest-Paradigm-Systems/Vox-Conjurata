import os
import sys
import subprocess
import requests
import google.generativeai as genai

def locate_gemini_key():
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ.get("GEMINI_API_KEY")
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                clean_line = line.strip()
                if clean_line.startswith("GEMINI_API_KEY="):
                    value = clean_line.split("=", 1)[1].strip()
                    return value.strip('"').strip("'")
    return None

GEMINI_KEY = locate_gemini_key()

if not GEMINI_KEY:
    print("❌ Error: GEMINI_API_KEY not found in system environment or your local .env file.")
    print("Please ensure GEMINI_API_KEY is defined in ~/vox-conjurata/.env")
    sys.exit(1)

genai.configure(api_key=GEMINI_KEY)

def select_active_model():
    """Programmatically queries the API to pick the best active model footprint available."""
    try:
        available_models = [
            m.name.split("/")[-1] 
            for m in genai.list_models() 
            if 'generateContent' in m.supported_generation_methods
        ]
        
        # Priority mapping: Prefer active Pro models, then fall back to Flash tiers
        for model_id in available_models:
            if "pro" in model_id and "vision" not in model_id:
                return model_id
        for model_id in available_models:
            if "flash" in model_id:
                return model_id
                
        return available_models[0] if available_models else "gemini-2.5-pro"
    except Exception:
        return "gemini-2.5-pro"  # Fail-safe operational default

ACTIVE_MODEL = select_active_model()
print(f"🌲 Connected to Gemini Remote Cloud Engine Engine (Target: {ACTIVE_MODEL})")
model = genai.GenerativeModel(ACTIVE_MODEL)

def get_browser_context():
    try:
        r = requests.get('http://localhost:8080/api/v1/diagnostics/latest', timeout=1)
        return r.json() if r.status_code == 200 else None
    except:
        return None

def run_and_heal(cmd_string):
    print(f"🚀 Executing target pipeline command: {cmd_string}")
    result = subprocess.run(cmd_string, shell=True, capture_output=True, text=True)
    
    browser_log = get_browser_context()
    is_browser_broken = browser_log and browser_log.get("status") != "nominal"

    if result.returncode != 0 or is_browser_broken:
        print("\n⚠️ Failure detected inside the pipeline! Querying Gemini for remediation...")
        
        prompt = f"""
        A tool inside a local development container environment failed. Fix it.
        Command Attempted: {cmd_string}
        Terminal Exit Code: {result.returncode}
        Terminal stdout: {result.stdout}
        Terminal stderr: {result.stderr}
        Browser Log Context: {browser_log}
        
        Analyze the failure path (stale lockfiles, container registry tag mismatches, etc.).
        Output ONLY a direct, valid bash command sequence or script capable of fixing the environment and re-running the tool successfully. Do not include markdown explanations.
        """
        try:
            ai_response = model.generate_content(prompt).text.strip()
            clean_fix = ai_response.replace("`" + "`" + "`bash", "").replace("`" + "`" + "`", "").strip()
            
            print(f"\n🤖 Gemini Remediation Patch Proposed:\n\n{clean_fix}\n")
            
            confirm = input("Do you want to execute this patch automatically? (y/N): ")
            if confirm.lower() == 'y':
                print("Running fix...")
                subprocess.run(clean_fix, shell=True)
            else:
                print("Patch aborted by user.")
        except Exception as e:
            print(f"Failed to communicate with Gemini API: {e}")
    else:
        print("✅ Command executed successfully with zero errors.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python heal.py <command>")
        sys.exit(1)
        
    run_and_heal(" ".join(sys.argv[1:]))
