
from waitress import serve
from webapp import app

if __name__ == "__main__":
    print("AI Software Company Simulator — always-on server")
    print("-> http://localhost:5000")
    serve(app, host="0.0.0.0", port=5000, threads=8)
