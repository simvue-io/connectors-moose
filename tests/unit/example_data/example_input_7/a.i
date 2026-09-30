  [BCs]
    [hot]
      !include b.i
      boundary = left
      value = 1000
    []
    [cold]
      !include b.i
      boundary = right
      value = 0
    []
  []