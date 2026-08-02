---
name: libreoffice-uno-list-validation
description: Recipe for adding an xlsx drop-down (list) data validation to a cell range over UNO.
metadata:
  type: reference
---

To give cells a drop-down list in Calc via UNO, read the range's `Validation` struct, mutate it, then
assign it BACK (mutating in place does nothing):

```python
val = rng.Validation                     # rng = sheet.getCellRangeByName("D2:D29")
val.Type = LIST                          # com.sun.star.sheet.ValidationType.LIST
val.ShowList = UNSORTED                  # TableValidationVisibility; 0 = INVISIBLE = no drop-down
val.IgnoreBlankCells = True
val.ShowErrorMessage = True; val.ErrorAlertStyle = STOP
val.setFormula1('"Pass";"Fail";"Held"')  # quoted strings, `;` separator (see [[libreoffice-uno-formula-separator]])
rng.Validation = val
```

`doc.store()` round-trips it to xlsx as `<dataValidation type="list" formula1="&quot;Pass,Fail,Held&quot;">`.
Verify with openpyxl `ws.data_validations.dataValidation`; note `showDropDown=False` there means the
drop-down IS shown (the xlsx attribute really means "suppress").

Related: [[libreoffice-uno-fallback]], [[libreoffice-uno-store-blocks-on-dialog]]
