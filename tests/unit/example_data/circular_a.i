!include circular_b.i

[Mesh]
  [generated]
    type = GeneratedMeshGenerator
    dim = 2
    nx = 10
    ny = 10
    xmax = 1
    ymax = 1
  []
[]

[Executioner]
  type = Transient
  end_time = 1
  dt = 0.1
[]
