import streamlit as st
import simpy
import random
import pandas as pd
import matplotlib.pyplot as plt


# -----------------------------
# Simulation Model
# -----------------------------
class FireDept:
    def __init__(self, env, full_staff, min_staff, absence_prob, wait_threshold):
        self.env = env
        self.full_staff = full_staff
        self.min_staff = min_staff
        self.absence_prob = absence_prob
        self.wait_threshold = wait_threshold

        self.staff = simpy.Container(env, init=full_staff, capacity=full_staff)

        self.mutual_aid_calls = 0
        self.total_calls = 0
        self.delayed_calls = 0
        self.total_wait_time = 0
        self.overload_events = 0

        # track daily staffing adjustments
        self.current_absent = 0

        env.process(self.staffing_manager())

    def staffing_manager(self):
        """Adjust staffing once per day based on absences"""
        while True:
            # return previously absent staff
            if self.current_absent > 0:
                yield self.staff.put(self.current_absent)
                self.current_absent = 0

            # determine new absences
            absent = sum(1 for _ in range(self.full_staff) if random.random() < self.absence_prob)

            # enforce minimum staffing
            max_removable = self.full_staff - self.min_staff
            absent = min(absent, max_removable)

            # remove staff from availability
            if absent > 0:
                yield self.staff.get(absent)
                self.current_absent = absent

            # wait 1 day
            yield self.env.timeout(1440)

    def handle_call(self, staff_needed, min_required, duration):
        self.total_calls += 1
        arrival = self.env.now

        # Try to get FULL staffing first
        full_request = self.staff.get(staff_needed)
        result = yield full_request | self.env.timeout(self.wait_threshold)

        used_staff = 0

        if full_request in result:
            # Got full staffing before timeout
            wait_time = self.env.now - arrival
            used_staff = staff_needed

        else:
            # Timeout occurred before full staffing available
            if not full_request.triggered:
                full_request.cancel()

            # Try minimum staffing
            if self.staff.level >= min_required:
                yield self.staff.get(min_required)
                wait_time = self.env.now - arrival
                used_staff = min_required
            else:
                # True failure → mutual aid
                self.mutual_aid_calls += 1
                return

        # Track delay
        if wait_time > 0:
            self.delayed_calls += 1
            self.total_wait_time += wait_time

        # Overload check at arrival
        if self.staff.level < staff_needed:
            self.overload_events += 1

        # Perform the call
        yield self.env.timeout(duration)

        # Release staff
        yield self.staff.put(used_staff)


def generate_call(ems_fraction):
    r = random.random()

    #basic EMS call, one ambulance and crew of 2
    if r < ems_fraction: 
        return 2, 2, random.uniform(45, 75)
    #fire calls except structure fire (alarms, MVC, etc.)
    elif r < 0.99: 
        return random.randint(2, 6), 2, random.uniform(40, 60)
    # structure fire!
    else:
        return random.randint(4, 6), 2, random.uniform(120, 240)

def get_call_rate(env_time, base_rate):
    hour = int((env_time // 60) % 24)

    #if 0 <= hour < 6:
    #    return base_rate * 0.3   # quiet overnight
    #elif 6 <= hour < 12:
    #    return base_rate * 1.2   # morning ramp
    #elif 12 <= hour < 18:
    #    return base_rate * 1.6   # peak daytime
    #elif 18 <= hour < 22:
    #    return base_rate * 1.0   # evening moderate
    #else:
    #    return base_rate * 0.5   # late night drop

    return base_rate
    
def call_generator(env, fd, calls_per_year, ems_fraction):
    base_rate = calls_per_year / (365 * 1440)

    while True:
        current_rate = get_call_rate(env.now, base_rate)
        yield env.timeout(random.expovariate(current_rate))
        staff_needed, min_required, duration = generate_call(ems_fraction)
        env.process(fd.handle_call(staff_needed, min_required, duration))


def run_simulation(full_staff, min_staff, absence_prob, ems_fraction, wait_threshold, calls_per_year, days=365):
    env = simpy.Environment()
    fd = FireDept(env, full_staff, min_staff, absence_prob, wait_threshold)

    env.process(call_generator(env, fd, calls_per_year, ems_fraction))
    env.run(until=days * 1440)

    avg_wait = (fd.total_wait_time / fd.delayed_calls) if fd.delayed_calls > 0 else 0

    return {
        "Total Calls": fd.total_calls,
        "Mutual Aid Calls": fd.mutual_aid_calls,
        "Mutual Aid %": (fd.mutual_aid_calls / fd.total_calls) * 100 if fd.total_calls > 0 else 0,
        "Delayed Calls": fd.delayed_calls,
        "Delayed %": (fd.delayed_calls / fd.total_calls) * 100 if fd.total_calls > 0 else 0,
        "Avg Delay (min)": avg_wait,
        "Overload Events": fd.overload_events,
        "Overload Probability %": (fd.overload_events / fd.total_calls) * 100 if fd.total_calls > 0 else 0
    }


# -----------------------------
# Streamlit UI
# -----------------------------
st.title("Hartford FD Staffing Model")

st.sidebar.header("Simulation Controls")

base_calls = st.sidebar.slider("Current Annual Call Volume", 2000, 6000, 3000, step=100)
growth_rate = st.sidebar.slider("Annual Call Growth Rate (%)", 0, 15, 5)

EMS_percentage = st.sidebar.slider("Percentage of EMS Calls", 50, 99, 70)

absence_prob = st.sidebar.slider("Daily Absence Probability per Person", 0.0, 0.5, 0.08)

wait_threshold = 30 #st.sidebar.slider("Coverage Threshold (minutes):", 0, 45, 30)

iterations = st.sidebar.slider("Simulation Runs", 10, 100, 33)

years = [0, 1, 2, 3, 4, 5]
total_calls_per_year = [int(base_calls * (1 + growth_rate/100)**year) for year in years]
staffing_options = [6, 7, 8]
minimum_staffing={6:5, 7:5, 8:6}

EMS_fraction = EMS_percentage*0.01

# Run simulations
if st.button("Run Simulation"):
    all_results = []
    
    for staffing in staffing_options:
        for year in years:
            calls = int(base_calls * (1 + growth_rate/100) ** year)
            
            aggregate = {
                "Total Calls": 0,
                "Mutual Aid Calls": 0,
                "Mutual Aid %": 0,
                "Delayed Calls": 0,
                "Delayed %": 0,
                "Avg Delay (min)": 0,
                "Overload Events": 0,
                "Overload Probability %": 0
            }

            for _ in range(iterations):
                sim_result = run_simulation(staffing, minimum_staffing[staffing], absence_prob, EMS_fraction, wait_threshold, calls)
                for key in aggregate:
                    aggregate[key] += sim_result[key]

            for key in aggregate:
                aggregate[key] /= iterations

            aggregate["Staffing"] = staffing
            aggregate["Year"] = year
            aggregate["Calls/Year"] = calls

            all_results.append(aggregate)

    df = pd.DataFrame(all_results)

    #st.subheader("Simulation Results")
    #xsst.dataframe(df)

    st.subheader("Total Calls")
    fig, ax = plt.subplots()
    ax.plot(years, total_calls_per_year, 'o--')
    ax.grid(axis='y')
    ax.set_xlim(-0.5,5.5)
    #ax.set_ylim(0,10)
    ax.set_ylabel("Number of Calls")
    ax.set_xlabel("Years from present")
    st.pyplot(fig)

    #st.subheader("Delayed Calls % Over Time")
    #st.line_chart(df.set_index("Year")["Delayed %"])

    #st.subheader("Overload Probability (%)")
    #st.line_chart(df.set_index("Year")["Overload Probability %"])

    #st.subheader("Average Delay (minutes)")
    #st.scatter_chart(df.set_index("Year")["Avg Delay (min)"])

    st.subheader("Calls to Mutual Aid")
    fig, ax = plt.subplots()
    for s in staffing_options:
        ax.plot(df[df['Staffing']==s]['Year'], df[df['Staffing']==s]['Mutual Aid %'], 'o--', label=str(s))
    ax.legend(title="Per Shift",loc='upper left')
    ax.grid(axis='y')
    ax.set_xlim(-0.5,5.5)
    #ax.set_ylim(0,10)
    ax.set_ylabel("Mutual Aid Percentage")
    ax.set_xlabel("Years from present")
    st.pyplot(fig)

    
st.markdown("---")
st.markdown("**How to interpret:**")
#st.markdown("- Overload Probability: Chance a call arrives when staffing is insufficient")
st.markdown("- Mutual Aid: Calls our department cannot handle and must go to our mutual aid partners.")
st.markdown("- The simulation looks at full staffing of 6, 7, or 8 personnel.  Below is the relationship between full and minimum staffing.")
st.markdown("""
| Full | Minimum |
|------|---------|
| 6    | 5       |
| 7    | 5       |
| 8    | 6       |
""")
st.markdown("- Absences reduce staffing daily but never below minimum.")
