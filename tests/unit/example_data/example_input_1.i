# File from https://mooseframework.inl.gov/modules/thermal_hydraulics/tutorials/basics/input_file.html
# Added file_base to Output section as this is a requirement for our connector

# radius of a flow channel
r = ${units 5 cm -> m}
# cross-sectioanl area
A_pipe = ${fparse pi * r * r}
# hydraulic diameter
D_h_pipe = ${fparse 2 * r}

[GlobalParams]
  initial_p = 1e5   # Pa
  initial_vel = 0   # m/2
  initial_T = 310   # K

  closures = simple
  rdg_slope_reconstruction = full
[]

[FluidProperties]
  [fluid]
    type = IdealGasFluidProperties
  []
[]

[Components]
  [pipe]
    type = FlowChannel1Phase
    position = '0 0 0'
    orientation = '1 0 0'
    length = 2
    A = ${A_pipe}
    D_h = ${D_h_pipe}
    n_elems = 25
    f = 0
    fp = fluid
  []

  [inlet]
    type = InletStagnationPressureTemperature1Phase
    input = 'pipe:in'
    p0 = 1.00001e5
    T0 = 310
  []

  [outlet]
    type = Outlet1Phase
    input = 'pipe:out'
    p = 1e5
  []
[]

[Postprocessors]
  [T_inlet]
    type = SideAverageValue
    boundary = pipe:in
    variable = T
  []
[]

[Preconditioning]
  [pc]
    type = SMP
    full = true
  []
[]

[Executioner]
  type = Transient
  start_time = 0
  end_time = 1
  dt = 0.1

  solve_type = NEWTON
  line_search = basic

  nl_max_its = 5
  nl_rel_tol = 1e-5
[]

[Outputs]
  file_base = results/example_input_1
  exodus = true
  csv = true
[]