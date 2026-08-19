  [BCs]
    [hot]
      type = DirichletBC
      variable = T
      boundary = left
      value = 1000
    []
    [cold]
      type = DirichletBC
      variable = T
      boundary = right
      value = 0
    []
  []