import re
from minio import Minio
from django.core.management.base import BaseCommand
from canvas.models import ChipSample, BedGraph, CNV, Report, Classification
import csv
import tempfile
from collections import defaultdict
from django.conf import settings


class Command(BaseCommand):
    help = "Associate files with ChipSample models"

    def add_arguments(self, parser):
        parser.add_argument("chip_id", type=str, help="The Chip ID to process")
        parser.add_argument(
            "bucket_name", type=str, help="MinIO bucket containing the media files"
        )
        parser.add_argument("--pdf", action="store_true", help="label for pdf")
        parser.add_argument(
            "--classification_ids", nargs="+", type=int, help="classification ids"
        )
        parser.add_argument("--cnv_pk", type=int, help="CNV ID to process specifically")

    def handle(self, *args, **options):
        chip_id = options["chip_id"]
        bucket_name = options["bucket_name"]
        pdf = options["pdf"]
        classification_ids = options["classification_ids"]
        cnv_pk = options["cnv_pk"]

        # MinIO client setup
        client = Minio(
            settings.MINIO_STORAGE_ENDPOINT,
            settings.MINIO_STORAGE_ACCESS_KEY,
            settings.MINIO_STORAGE_SECRET_KEY,
            secure=False,
        )

        def list_files(prefix):
            return [
                i.object_name
                for i in client.list_objects(bucket_name, prefix, recursive=True)
            ]

        def download_file(object_name):
            tmp_file = tempfile.NamedTemporaryFile(delete=False)
            client.fget_object(bucket_name, object_name, tmp_file.name)
            return tmp_file.name

        def extract_position(filename, cnv_pk=None):
            """Extracts position from filenames using regex like 'R02C03' or 'R01C04'."""
            match = re.search(r"R\d{2}C\d{2}", filename)
            if cnv_pk:
                if str(cnv_pk) in filename:
                    return f"{match.group(0)}_{cnv_pk}" if match else None
            else:
                return match.group(0) if match else None

        def gather_scoresheets(cnv_pk=None):
            """
            Returns a dictionary of scoresheets in the format:
            {
                "R03C02": "path to the scoresheet",
            }
            """
            files = list_files(f"chip_data/{chip_id}/ClassifyCNV/")
            return {
                extract_position(file, cnv_pk): download_file(file)
                for file in files
                if file.endswith("Scoresheet.txt") and extract_position(file)
            }

        def gather_cnvs(cnv_pk=None):
            """
            Returns a dictionary of CNV files in the format:
            {
                "R03C02": "path to the cnvs",
            }
            """
            files = list_files(f"chip_data/{chip_id}/cnvs/")
            return {
                extract_position(file, cnv_pk): download_file(file)
                for file in files
                if "iscn" in file and extract_position(file)
            }

        def gather_bedgraphs():
            """
            Returns a dictionary of BedGraph file paths in the format:
            {
                "R03C02": ["path to bedgraph1", "path to bedgraph2", ...],
            }
            """
            files = list_files(f"chip_data/{chip_id}/bedgraphs/")
            bedgraph_dict = defaultdict(list)  # Initialize a defaultdict of lists
            for file in files:
                position = extract_position(file)
                if position:
                    bedgraph_dict[position].append(
                        file
                    )  # Append the file path to the list for that position
            return bedgraph_dict

        def process_cnv_file(file_path):
            cnv_data = {}
            with open(file_path, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    (
                        chr_info,
                        numsnp_info,
                        length_info,
                        state_info,
                        file_info,
                        startsnp_info,
                        endsnp_info,
                        conf,
                        iscn,
                    ) = parts
                    region = chr_info.split(":")
                    chr_start_end = f"{region[0]}_{region[1].split('-')[0]}_{region[1].split('-')[1]}"
                    cnv = {
                        "iscn": iscn,
                        "chr_info": chr_info,
                        "numsnp_info": numsnp_info,
                        "length_info": length_info,
                        "state_info": state_info,
                        "file_info": file_info,
                        "startsnp_info": startsnp_info,
                        "endsnp_info": endsnp_info,
                        "conf": conf,
                    }
                    cnv_data[chr_start_end] = cnv
            return cnv_data

        def process_scoresheet_file(file_path):
            scoresheet_data = {}
            with open(file_path, "r") as f:
                reader = csv.DictReader(f, delimiter="\t")
                for row in reader:
                    variant_id = row["VariantID"]
                    clean_variant_id = variant_id.replace("_DEL", "").replace(
                        "_DUP", ""
                    )
                    scoresheet_data[clean_variant_id] = dict(row)
            return scoresheet_data

        def gather_pdfs():
            """
            Gathers and returns a dictionary of PDF file paths, keyed by position.
            Format:
            {
                "R03C02": ["path to pdf1", "path to pdf2", ...],
            }
            """
            pdf_files = list_files(f"chip_data/{chip_id}/pdfs/")
            pdf_dict = defaultdict(list)
            for file in pdf_files:
                position = extract_position(file)
                if position and file.endswith(".pdf"):
                    pdf_dict[position].append(
                        file
                    )  # Save MinIO path without downloading
            return pdf_dict

        def associate_pdfs():
            """
            Associates gathered PDF files to the ChipSample entries.
            Only executes if the --pdf flag is provided.
            """
            pdf_files = gather_pdfs()
            for position, pdf_paths in pdf_files.items():
                try:
                    chipsample = ChipSample.objects.get(
                        chip__chip_id=chip_id, position=position
                    )
                except ChipSample.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(
                            f"ChipSample not found for position {position}"
                        )
                    )
                    continue

                for pdf_path in pdf_paths:
                    # Assuming you have a field to store the MinIO path for the PDF in ChipSample or related model
                    report, created = Report.objects.get_or_create(
                        chipsample=chipsample,
                        report=pdf_path,  # Save the MinIO path without downloading
                    )
                    if classification_ids:
                        classifications = Classification.objects.filter(
                            id__in=classification_ids
                        )
                        report.classifications.add(*classifications)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Added {len(classifications)} classifications to report {pdf_path}."
                            )
                        )
                    if created:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Saved Report {pdf_path} to {chipsample}"
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Report {pdf_path} for {chipsample} already exists."
                            )
                        )

        if cnv_pk:
            try:
                cnv = CNV.objects.get(pk=cnv_pk)
                chipsample = cnv.chipsample
                position = chipsample.position
                position = f"{position}_{cnv_pk}"

                scoresheet_files = gather_scoresheets(cnv_pk)
                cnv_files = gather_cnvs(cnv_pk)

                if scoresheet_files[position] and cnv_files[position]:
                    cnv_data = {}
                    with open(cnv_files[position], "r") as f:
                        for line in f:
                            parts = line.strip().split()
                            variant_id, cn, numsnp, iscn = parts
                            start = variant_id.split("_")[1]
                            end = variant_id.split("_")[2]
                            length = int(end) - int(start)
                            cnv_data[variant_id] = {
                                "iscn": iscn,
                                "state_info": cn,
                                "length_info": length,
                                "numsnp_info": numsnp,
                            }

                    scoresheet_data = process_scoresheet_file(
                        scoresheet_files[position]
                    )

                    for variant_id, cnv_dict in cnv_data.items():
                        score_dict = scoresheet_data.get(variant_id, {})
                        merged_dict = {**cnv_dict, **score_dict}
                        cnv.variant_id = variant_id
                        cnv.cnv_json.update(merged_dict)
                        cnv.save()
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Updated CNV {variant_id} for {chipsample}"
                            )
                        )

                bedgraphs = gather_bedgraphs()
                if position in bedgraphs:
                    for bedgraph_path in bedgraphs[position]:
                        bedgraph_type = bedgraph_path.split(".")[1]
                        if bedgraph_type in dict(BedGraph.bedgraph_types):
                            bg, created = BedGraph.objects.get_or_create(
                                chipsample=chipsample,
                                bedgraph_type=bedgraph_type,
                                bedgraph=bedgraph_path,
                            )
                            if created:
                                self.stdout.write(
                                    self.style.SUCCESS(
                                        f"Saved BedGraph {bedgraph_path} to {chipsample}"
                                    )
                                )

                return

            except CNV.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"CNV with ID {cnv_pk} not found"))
                return
        # Use the associate_pdfs function only if --pdf flag is provided
        if pdf:
            associate_pdfs()
            exit()

        associate_pdfs()

        scoresheet_files = gather_scoresheets()
        cnv_files = gather_cnvs()

        for position, scoresheet_file in scoresheet_files.items():
            cnv_data = process_cnv_file(cnv_files[position])
            scoresheet_data = process_scoresheet_file(scoresheet_file)

            cnvs = {}
            for variant_id, cnv_dict in cnv_data.items():
                score_dict = scoresheet_data.get(variant_id, {})
                merged_dict = {**cnv_dict, **score_dict}
                cnvs[variant_id] = merged_dict

            try:
                chipsample = ChipSample.objects.get(
                    chip__chip_id=chip_id, position=position
                )
            except ChipSample.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(
                        f"ChipSample not found for {chip_id} position {position}"
                    )
                )
                continue

            for variant_id, cnv in cnvs.items():
                cnv, created = CNV.objects.get_or_create(
                    variant_id=variant_id, cnv_json=cnv, chipsample=chipsample
                )
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f"Saved CNV {variant_id} to {chipsample}")
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"CNV {variant_id} for {chipsample} already exists."
                        )
                    )

        bedgraphs = gather_bedgraphs()
        for (
            position,
            bedgraph_paths,
        ) in bedgraphs.items():  # Note that bedgraph_paths is now a list
            try:
                chipsample = ChipSample.objects.get(
                    chip__chip_id=chip_id, position=position
                )
            except ChipSample.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"ChipSample not found for position {position}")
                )
                continue

            for (
                bedgraph_path
            ) in bedgraph_paths:  # Iterate over the list of bedgraph paths
                bedgraph_type = bedgraph_path.split(".")[1]
                if bedgraph_type in dict(
                    BedGraph.bedgraph_types
                ):  # Validate against allowed types
                    bg, created = BedGraph.objects.get_or_create(
                        chipsample=chipsample,
                        bedgraph_type=bedgraph_type,
                        bedgraph=bedgraph_path,  # Save the MinIO path without downloading
                    )
                    if created:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Saved BedGraph {bedgraph_path} to {chipsample}"
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"BedGraph {bedgraph_path} for {chipsample} already exists."
                            )
                        )
                else:
                    self.stdout.write(
                        self.style.ERROR(f"Invalid bedgraph type: {bedgraph_type}")
                    )

        # Process quality metrics
        gt_sample_summary = list_files(
            f"chip_data/{chip_id}/gtcs/gt_sample_summary.csv"
        )[0]
        gt_sample_summary_path = download_file(gt_sample_summary)

        with open(gt_sample_summary_path, "r") as f:
            samples = [i.split(",") for i in f.read().splitlines()[1:]]
            for sample in samples:
                # Extract position using regex
                position = extract_position(sample[0])
                if not position:
                    self.stdout.write(
                        self.style.ERROR(f"Position not found in {sample[0]}")
                    )
                    continue

                try:
                    chipsample = ChipSample.objects.get(
                        chip__chip_id=chip_id, position=position
                    )
                    chipsample.autosomal_call_rate = sample[3]
                    chipsample.call_rate = sample[4]
                    chipsample.lrr_std_dev = sample[5]
                    chipsample.sex_estimate = sample[6]
                    chipsample.save()
                except ChipSample.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(
                            f"ChipSample not found for position {position}"
                        )
                    )
                    continue
                self.stdout.write(
                    self.style.SUCCESS(f"Updated quality metrics for {chipsample}")
                )
