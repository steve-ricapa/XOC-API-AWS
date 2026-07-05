import os

import boto3

from src.shared.tickets_store import build_secondary_index_fields


def main() -> None:
    table_name = os.environ["TICKETS_TABLE_NAME"]
    table = boto3.resource("dynamodb").Table(table_name)
    last_evaluated_key = None
    updated = 0

    while True:
        scan_kwargs = {}
        if last_evaluated_key:
            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            ticket_id = item.get("ticket_id")
            tenant_id = item.get("tenant_id")
            status = item.get("status")
            created_at = item.get("created_at") or item.get("updated_at")
            if not ticket_id or tenant_id is None or not status or not created_at:
                continue

            secondary = build_secondary_index_fields(int(tenant_id), str(ticket_id), str(status).strip().upper(), created_at)
            needs_update = any(item.get(key) != value for key, value in secondary.items())
            if not needs_update:
                continue

            table.update_item(
                Key={"pk": item["pk"], "sk": item["sk"]},
                UpdateExpression="SET #gsi1pk = :gsi1pk, #gsi1sk = :gsi1sk, #gsi2pk = :gsi2pk, #gsi2sk = :gsi2sk, #gsi3pk = :gsi3pk, #gsi3sk = :gsi3sk",
                ExpressionAttributeNames={
                    "#gsi1pk": "gsi1pk",
                    "#gsi1sk": "gsi1sk",
                    "#gsi2pk": "gsi2pk",
                    "#gsi2sk": "gsi2sk",
                    "#gsi3pk": "gsi3pk",
                    "#gsi3sk": "gsi3sk",
                },
                ExpressionAttributeValues={
                    ":gsi1pk": secondary["gsi1pk"],
                    ":gsi1sk": secondary["gsi1sk"],
                    ":gsi2pk": secondary["gsi2pk"],
                    ":gsi2sk": secondary["gsi2sk"],
                    ":gsi3pk": secondary["gsi3pk"],
                    ":gsi3sk": secondary["gsi3sk"],
                },
            )
            updated += 1

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    print(f"Updated {updated} ticket items in {table_name}")


if __name__ == "__main__":
    main()
