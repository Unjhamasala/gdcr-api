from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "root works"}

@app.get("/random")
def random_endpoint(value: str):
    return {
        "received": value,
        "status": "ok",
        "number": 123
    }
