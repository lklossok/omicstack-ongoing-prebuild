from flask import Flask, jsonify, request, render_template, redirect, url_for
import flask_login
from google.cloud import storage
from google.auth.transport.requests import Request

import json
import datetime
import google.auth
import os
import zipfile

# load OmicStackUsers Secret from gcloud
USERS_JSON = os.environ.get("USERS")
# load secret key from gcloud
SECRET_KEY = os.environ.get("KEY")

app = Flask(__name__)
app.secret_key = SECRET_KEY

storage_client = storage.Client()

login_manager = flask_login.LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class User(flask_login.UserMixin):
    def __init__(self, email, password):
        self.id = email
        self.password = password

users = {email: User(email, password) for email, password in json.loads(USERS_JSON).items()}

@login_manager.user_loader
def user_loader(id):
    return users.get(id)

@app.get("/login")
def login():
    return render_template("login.html")

@app.post("/login")
def login_post():
    try:
        credentials, _ = google.auth.default()
        credentials.refresh(Request())

        email = request.form["email"]
        password = request.form["password"]

        user = users.get(email)

        if user is None:
            return jsonify({"error": "invalid email"}), 401
        
        if password != user.password:
            return jsonify({"error": "invalid password"}), 401
        
        flask_login.login_user(user)

        return redirect(url_for("index"))
    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500

@app.route("/")
@flask_login.login_required
def index():
    return render_template("index.html", user=flask_login.current_user.get_id())

@app.get("/generate-upload-url")
@flask_login.login_required
def generate_upload_url():
    try:
        credentials, _ = google.auth.default()
        credentials.refresh(Request())

        filename = request.args.get("filename")
        if not filename:
            return jsonify({"error": "filename required"}), 400

        pipeline = request.args.get("pipeline", "vcf") # if nothing default to vcf

        # associate bucket with username (email before the "@")
        name = flask_login.current_user.get_id().split("@")[0]
        bucket = storage_client.bucket(name)
        if storage_client.lookup_bucket(name) is None:
            storage_client.create_bucket(name, location="us-central1")
        blob = bucket.blob(f"uploads/{filename}")

        # Add Job metadata
        job = {
            "filename": filename,
            "pipeline": pipeline,
            "status": "pending",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        job_blob = bucket.blob(f"jobs/{filename}.json")
        job_blob.upload_from_string(
            json.dumps(job),
            content_type="application/json"
        )

        expiration = datetime.timedelta(hours=1)

        url = blob.generate_signed_url(
            version="v4",
            expiration=expiration,
            method="PUT",
            service_account_email="368095197401-compute@developer.gserviceaccount.com",
            access_token=credentials.token
        )

        return jsonify({
            "filename": filename,
            "pipeline": pipeline,
            "upload_url": url,
            "expires_in": str(expiration)
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


@app.route("/offload")
@flask_login.login_required
def offload():
    return render_template("offload.html", user=flask_login.current_user.get_id())

@app.get("/generate-offload-url")
@flask_login.login_required
def generate_offload_url():
    try:
        credentials, _ = google.auth.default()
        credentials.refresh(Request())

        sample = request.args.get("sample")
        if not sample:
            return jsonify({"error": "sample required"}), 400

        # associate bucket with username (email before the "@")
        name = flask_login.current_user.get_id().split("@")[0]
        bucket = storage_client.bucket(name)
        if storage_client.lookup_bucket(name) is None:
            return jsonify({"error": "no bucket exists for your account"}), 400

        # download the results folder, zip it, and reupload
        contents = bucket.list_blobs(prefix="results/", delimiter="/")
        with zipfile.ZipFile("results.zip", "w", zipfile.ZIP_DEFLATED) as results:
            for file in contents:
                data = file.download_as_bytes()
                results.writestr(file.name, data)
        blob = bucket.blob(f"results.zip")
        blob.upload_from_filename("results.zip")

        # Add tracker metadata
        job = {
            "sample": sample,
            "user": flask_login.current_user.get_id(),
            "created_at": datetime.datetime.utcnow().isoformat()
        }

        job_blob = bucket.blob(f"offload_tracker/{sample}.json")
        job_blob.upload_from_string(
            json.dumps(job),
            content_type="application/json"
        )

        expiration = datetime.timedelta(hours=1)

        url = blob.generate_signed_url(
            version="v4",
            expiration=expiration,
            method="GET",
            service_account_email="368095197401-compute@developer.gserviceaccount.com",
            access_token=credentials.token
        )

        return jsonify({
            "sample": sample,
            "user": flask_login.current_user.get_id(),
            "offload_url": url,
            "expires_in": str(expiration)
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
