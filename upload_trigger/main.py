import json
import uuid
from google.cloud import storage, batch_v1
from google.api_core.exceptions import AlreadyExists

storage_client = storage.Client()
batch_client = batch_v1.BatchServiceClient()

PROJECT = "omicstack"
LOCATION = "us-central1"
BUCKET = "omicstack-signed-urls"


def write_samplesheet(sample, fastq1, fastq2):
    path = f"samplesheets/{sample}.tsv"
    blob = storage_client.bucket(BUCKET).blob(path)

    status = "1" # TO-for now
    lane = "L001" # temporary, TO-for now


    content = (
        "patient\tlane\tstatus\tsample\tfastq_1\tfastq_2\n"
        f"{sample}\tL001\t1\t{sample}\t{fastq1}\t{fastq2}\n"
    )
    content_type = "text/tab-separated-values"

    print(f"Samplesheet: {content}")

    blob.upload_from_string(content, content_type=content_type)
    return f"gs://{BUCKET}/{path}"


def build_batch_job(sample, samplesheet_uri, step):
    return batch_v1.Job(
        task_groups=[
            batch_v1.TaskGroup(
                task_spec=batch_v1.TaskSpec(
                    runnables=[
                        batch_v1.Runnable(
                            container=batch_v1.Runnable.Container(
                                image_uri="us-central1-docker.pkg.dev/omicstack/omicstack/sarek_v1:latest",
                                commands=[
                                    "bash",
                                    "-c",
                                    f"""
                                    nextflow run nf-core/sarek \
                                    -profile docker \
                                    -c nextflow.config \
                                    --input {samplesheet_uri} \
                                    {step} \
                                    --outdir gs://omicstack-signed-urls/results/{sample} \
                                    -resume 
                                    """
                                ], 
                            )
                        )
                    ],
                    compute_resource=batch_v1.ComputeResource(
                        cpu_milli=4000,
                        memory_mib=16384,
                    ),
                )
            )
        ],
        logs_policy=batch_v1.LogsPolicy(
            destination=batch_v1.LogsPolicy.Destination.CLOUD_LOGGING
        ),
    )


def on_upload(event, context):
    filename = event["name"]

    if not filename.startswith("uploads/"):
        return

    bucket = storage_client.bucket(BUCKET)

    filename_r1 = None
    filename_r2 = None

    # Paired-end
    if filename.endswith("_1.fastq.gz"):
        filename_r1 = filename
        filename_r2 = filename.replace("_1.fastq.gz", "_2.fastq.gz")

        blob_r2 = bucket.blob(filename_r2)
        if not blob_r2.exists():
            print(f"Waiting for pair: {filename_r2}")
            return

        print(f"Paired-end detected: {filename_r1}, {filename_r2}")

        sample = filename.split("/")[-1].replace("_1.fastq.gz", "")

    # R2 upload (ignore)
    elif filename.endswith("_2.fastq.gz"):
        print("R2 uploaded first or separately, skipping trigger")
        return

    else:
        print("Not a paired-end FASTQ, skipping")
        return

    # Load job metadata
    job_file = filename_r1.replace("uploads/", "jobs/") + ".json"
    job_blob = bucket.blob(job_file)

    if not job_blob.exists():
        print("No job metadata found")
        return

    job_meta = json.loads(job_blob.download_as_text())

    if job_meta.get("status") == "submitted":
        print("Already submitted, skipping")
        return

    pipeline = job_meta.get("pipeline", "variant_calling")

    if pipeline == "bam":
        step = "--tools bwa" # need to fix this later
    else:
        step = "--tools mutect2"

    print(f"Running {pipeline} pipeline for {sample}")

    # Build FASTQ paths 
    fastq1 = f"gs://{BUCKET}/{filename_r1}"
    fastq2 = f"gs://{BUCKET}/{filename_r2}" 

    # Build samplesheet
    samplesheet = write_samplesheet(sample, fastq1, fastq2)

    # Build and submit batch job
    batch_job = build_batch_job(sample, samplesheet, step)

    lower_sample = sample.lower()
    job_id = f"sarek-{lower_sample}-{uuid.uuid4().hex[:8]}"

    try:
        batch_client.create_job(
            parent=f"projects/{PROJECT}/locations/{LOCATION}",
            job=batch_job,
            job_id=job_id,
        )
        print("Job submitted:", job_id)

    except AlreadyExists:
        print("Job already exists, skipping")
        return

    # Update job metadata
    job_meta["status"] = "submitted"
    job_meta["batch_job_id"] = job_id

    job_blob.upload_from_string(
        json.dumps(job_meta),
        content_type="application/json"
    )
