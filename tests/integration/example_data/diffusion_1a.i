!include diffusion_1d.i

[Mesh]
  type = GeneratedMesh
  dim = 2
  nx = 20
  ny = 20
[]

[BCs]
  [left]
    type = DirichletBC
    variable = u
    boundary = left
    value = 0
  []
[]

[Executioner]
  type = Steady
  solve_type = 'PJFNK'
[]