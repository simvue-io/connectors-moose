# File from https://mooseframework.inl.gov/getting_started/examples_and_tutorials/examples/ex01_inputfile.html
# Added file_base to Output section as this is a requirement for our connector

[Mesh]
  # We use a pre-generated mesh file (in exodus format).
  # This mesh file has 'top' and 'bottom' named boundaries defined inside it.
  #file = mug_2.e
  file = mug.e
[]

[Variables]
  [./diffused]
    order = FIRST
    family = LAGRANGE
  [../]
[]

[Kernels]
  [./diff]
    type = Diffusion
    variable = diffused
  [../]
[]

[BCs]
  [./bottom] # arbitrary user-chosen name
    type = DirichletBC
    variable = diffused
    boundary = 'bottom' # This must match a named boundary in the mesh file
    value = 1
  [../]

  [./top] # arbitrary user-chosen name
    type = DirichletBC
    variable = diffused
    boundary = 'top' # This must match a named boundary in the mesh file
    value = 0
  [../]
[]

[Executioner]
  type = Steady
  solve_type = 'PJFNK'
[]

[Outputs]
  file_base = results/example_input_2
  execute_on = 'timestep_end'
  exodus = true
[]