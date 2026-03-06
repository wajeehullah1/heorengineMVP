# test_connection.R — Verify the Python-to-R bridge works.
#
# Usage:  Rscript r/test_connection.R <path_to_json_file>
#
# Expects JSON like {"a": 3, "b": 5} and returns
# {"sum": 8, "product": 15, "message": "R connection working"}

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 1) {
  cat("Error: no JSON file path provided\n", file = stderr())
  quit(status = 1)
}

json_file <- args[1]

if (!file.exists(json_file)) {
  cat(paste0("Error: file not found: ", json_file, "\n"), file = stderr())
  quit(status = 1)
}

# --- Read and parse JSON ------------------------------------------------
tryCatch({
  library(jsonlite)
  params <- fromJSON(json_file)
}, error = function(e) {
  cat(paste0("Error: invalid JSON — ", e$message, "\n"), file = stderr())
  quit(status = 1)
})

# --- Validate required parameters ----------------------------------------
if (is.null(params$a) || is.null(params$b)) {
  cat("Error: missing required parameters 'a' and/or 'b'\n", file = stderr())
  quit(status = 1)
}

# --- Calculate -----------------------------------------------------------
sum_val     <- params$a + params$b
product_val <- params$a * params$b

results <- list(
  sum     = sum_val,
  product = product_val,
  message = "R connection working"
)

cat(toJSON(results, auto_unbox = TRUE))
