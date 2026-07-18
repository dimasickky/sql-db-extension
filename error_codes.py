"""App-declared structured error codes for sql-db.

These pair with the platform taxonomy (`imperal_sdk.chat.error_codes`) for
cases that taxonomy doesn't cover — problems specific to reaching/using a
*user's* MySQL/MariaDB connection, not the Imperal backend itself. Every
code here matches the SDK's app-declared pattern `^[A-Z][A-Z0-9_]{2,63}$`
(imperal_sdk.types.action_result.ActionResult.error).

Platform codes (imported directly where they apply — validation, internal,
permission, rate limit, backend 5xx) are used as-is; these DB_* codes only
exist where no platform code honestly fits.
"""

DB_NO_ACTIVE_CONNECTION = "DB_NO_ACTIVE_CONNECTION"      # no connection selected/resolved for this request
DB_CONNECTION_NOT_FOUND = "DB_CONNECTION_NOT_FOUND"      # named/id'd connection doesn't exist
DB_CONNECTION_FAILED = "DB_CONNECTION_FAILED"            # connect attempt itself failed (bad host/creds/network)
DB_SCHEMA_NOT_CACHED = "DB_SCHEMA_NOT_CACHED"            # get_schema() hasn't been run yet, cache is cold
DB_TABLE_NOT_FOUND = "DB_TABLE_NOT_FOUND"                # referenced table doesn't exist in the schema
DB_COLUMN_NOT_FOUND = "DB_COLUMN_NOT_FOUND"              # referenced column doesn't exist on the table
DB_QUERY_FAILED = "DB_QUERY_FAILED"                      # the DB itself rejected/failed the SQL (translated detail)
DB_ZERO_ROWS_AFFECTED = "DB_ZERO_ROWS_AFFECTED"          # DML executed but matched/changed nothing
DB_SAVED_QUERY_NOT_FOUND = "DB_SAVED_QUERY_NOT_FOUND"    # run_saved/delete_saved on an unknown saved query id
