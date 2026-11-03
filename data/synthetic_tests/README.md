# Synthetic Test Fixtures for Self-Healing Data Pipeline

This folder contains a test suite of synthetic files designed for manual validation of the complete user journey in the UI.

## Files

| File | Purpose | Expected UI Behavior |
| :--- | :--- | :--- |
| `ecommerce_v1.yaml` | New Data Contract (YAML format) | Upload preview parses 5 columns; register as `ecommerce_v1`. |
| `ecommerce_v1.json` | New Data Contract (JSON format alternative) | Same schema as YAML; proves JSON format support. |
| `01_ecommerce_clean_valid.csv` | **Case 1: Clean & Valid Run** | 100% Pandera validation pass; zero drift; terminal green status. |
| `02_ecommerce_renamed_column.csv` | **Case 2: Renamed Column** (`total_amount`) | Drift detected; LLM proposes column alias patch; human approval gate. |
| `03_ecommerce_type_drift.csv` | **Case 3: Type Drift** (Currency `$`) | Drift detected; LLM proposes type coercion patch. |
| `04_ecommerce_missing_column.csv` | **Case 4: Missing Required Column** (`status`) | Drift detected; flags missing column; prompts healing. |
| `05_ecommerce_constraint_violation.csv`| **Case 5: Value Constraint Violation** | Value checks fail (`amount < 0`, `category: Automotive`); escalation. |
| `06_invalid_schema.yaml` | **Negative Contract Validation** | Instant red validation errors; Registration button disabled. |

---

## Step-by-Step Manual Validation Guide

### Step 1: Upload and Register the Contract
1. Navigate to **Data Contracts** (`http://localhost:5173/contracts`).
2. Click **"+ Upload contract"**.
3. Choose `ecommerce_v1.yaml` (or `ecommerce_v1.json`).
4. Enter contract name: `ecommerce_v1`.
5. Review the parsed columns (`order_id`, `customer_id`, `category`, `amount`, `status`) and constraints.
6. Click **"Register contract"**.
7. Observe that `ecommerce_v1` appears in the contract list and the TopBar contract dropdown refreshes automatically!

*(Optional Negative Test)*: Try uploading `06_invalid_schema.yaml`. Observe that red validation errors appear immediately and the register button is disabled.

---

### Step 2: Validate a Clean Run (Case 1)
1. Navigate to **Data Ingestion** (`http://localhost:5173/ingestion`).
2. Upload `01_ecommerce_clean_valid.csv`.
3. Verify the instant preview shows 8 rows with inferred types (`order_id: integer`, `amount: float`, etc.).
4. In the **Contract** selector, choose `ecommerce_v1`.
5. Click **"Run Pipeline"**.
6. The app automatically navigates to **`/runs/<run_id>`**.
7. Watch the execution:
   - Profiling $\rightarrow$ Pandera Validation $\rightarrow$ Complete!
   - Result: **CLEAN (GREEN)** with 0 drift events and no human approval needed.

---

### Step 3: Validate Self-Healing on Renamed Column (Case 2)
1. Navigate back to **Data Ingestion** (`http://localhost:5173/ingestion`).
2. Upload `02_ecommerce_renamed_column.csv` (contains `total_amount` instead of `amount`).
3. Select contract `ecommerce_v1` and click **"Run Pipeline"**.
4. Observe the run detail:
   - Pandera flags schema validation drift.
   - LLM Drift Diagnosis node detects that `total_amount` corresponds to `amount`.
   - Proposes a patch: `{"rename": {"total_amount": "amount"}}`.
   - Pipeline interrupts for **Human Approval**.
5. Click **"Approve"** in the UI to resume the pipeline and observe the patch applied and the run complete successfully!

---

### Step 4: Validate Other Drift & Constraint Cases
- Repeat with `03_ecommerce_type_drift.csv` to observe string-to-float coercion healing.
- Repeat with `04_ecommerce_missing_column.csv` to observe missing column diagnosis.
- Repeat with `05_ecommerce_constraint_violation.csv` to observe boundary violation handling.
