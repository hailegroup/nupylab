from pymeasure.instruments.proterial import rod4
r = rod4.ROD4("ASRL4::INSTR")
print(r.ch_1.mfc_range)
r.ch_1.setpoint = 50
print(r.ch_1.setpoint)