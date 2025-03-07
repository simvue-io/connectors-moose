# File from https://mooseframework.inl.gov/getting_started/examples_and_tutorials/examples/ex07_ics.html
# Added file_base to Output section as this is a requirement for our connector

[Mesh]
  file = half-cone.e
[]

[Variables]
  [./diffused]
    order = FIRST
    family = LAGRANGE

    # Use the initial Condition block underneath the variable
    # for which we want to apply this initial condition
    [./InitialCondition]
      type = ExampleIC
      coefficient = 2.0
    [../]
  [../]
[]

[Kernels]
  [./td]
    type = TimeDerivative
    variable = diffused
  [../]

  [./diff]
    type = Diffusion
    variable = diffused
  [../]
[]

[BCs]
  [./left]
    type = DirichletBC
    variable = diffused
    boundary = 'top'
    value = 2
  [../]

  [./right]
    type = DirichletBC
    variable = diffused
    boundary = 'bottom'
    value = 8
  [../]
[]

[Executioner]
  type = Transient
  dt = 0.1
  start_time = 0
  num_steps = 10

  #Preconditioned = JFNK (default)
  solve_type = 'PJFNK'


[]

[Outputs]
  file_base = results/example_input_3
  # Request that we output the initial condition so we can inspect
  # the values with our visualization tool
  exodus = true
[]