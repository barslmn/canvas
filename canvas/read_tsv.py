import csv
from datetime import datetime
from canvas.models import Institution, SampleType


def read_sample_from_tsv(file_path):
    # Initialize the data list
    data_list = []

    try:
        # Open the TSV file with UTF-8 encoding to support Turkish characters
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")

            # Retrieve the actual headers from the TSV file
            headers = reader.fieldnames
            if headers is None:
                raise ValueError("The TSV file is empty or malformed.")

            # Iterate over each row in the TSV file
            for row_number, row in enumerate(
                reader, start=2
            ):  # Start at 2 considering header row
                # Extract data based on available columns
                prot_id = row.get("prot_id")
                inst = row.get("inst")
                arrival = row.get("arrival_date")
                sex = row.get("sex")  # May be None if 'sex' column is missing
                sample_type = row.get(
                    "sample_type"
                )  # May be None if 'sample_type' column is missing

                # Validate 'inst' field
                if inst:
                    # Attempt to find a unique Institution matching the first 5 characters (case-insensitive)
                    inst_validated_qs = Institution.objects.filter(
                        name__icontains=inst[:5]
                    )
                    if inst_validated_qs.count() == 1:
                        inst_validated = inst_validated_qs.first()
                    else:
                        # Ambiguous or no match found
                        inst_validated = None
                else:
                    # 'inst' not found in existing institutions
                    inst_validated = None

                # Validate 'sample_type' field
                if sample_type:
                    # Attempt to find a unique Sample_typeitution matching the first 8 characters (case-insensitive)
                    sample_type_validated_qs = SampleType.objects.filter(
                        name__icontains=sample_type[:8]
                    )
                    if sample_type_validated_qs.count() == 1:
                        sample_type_validated = sample_type_validated_qs.first()
                    else:
                        # Ambiguous or no match found
                        sample_type_validated = None
                else:
                    # 'sample_type' not found in existing sample_typeitutions
                    sample_type_validated = None

                # Format 'arrival_date' if it's a datetime object or a valid date string
                if isinstance(arrival, datetime):
                    arrival_formatted = arrival.strftime("%Y-%m-%d")
                elif isinstance(arrival, str):
                    # If it's already a string, you might want to validate or reformat it
                    try:
                        # Attempt to parse and reformat
                        arrival_dt = datetime.strptime(arrival, "%Y-%m-%d")
                        arrival_formatted = arrival_dt.strftime("%Y-%m-%d")
                    except ValueError:
                        # If parsing fails, keep it as is or handle accordingly
                        arrival_formatted = arrival
                else:
                    # Handle other possible types (e.g., None)
                    arrival_formatted = ""

                # Create a dictionary for the current row
                row_dict = {
                    "prot_id": prot_id.strip() if isinstance(prot_id, str) else prot_id,
                    "inst": inst_validated,
                    "arrival_date": arrival_formatted,
                    "sex": sex.strip() if isinstance(sex, str) else sex,
                    "sample_type": sample_type_validated,
                }
                data_list.append(row_dict)

    except FileNotFoundError:
        raise FileNotFoundError(f"The file at path '{file_path}' was not found.")
    except UnicodeDecodeError:
        raise UnicodeDecodeError(
            "The file encoding is not UTF-8. Please ensure the TSV file is UTF-8 encoded."
        )
    except Exception as e:
        # Re-raise any other exceptions with additional context
        raise Exception(f"An error occurred while processing the TSV file: {str(e)}")

    return data_list
