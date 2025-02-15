# Script to download EDA files from Empatica website, format them by date/time, and read them into Ledalab
# unzip downloaded files

# the following packages are for opening/reading files and analyzing data
import os
import sys
import zipfile
import shutil
import pandas as pd
import numpy as np
import cvxopt as cv
import cvxopt.solvers
import statistics
from statistics import mean

# the following packages are for plotting
import pylab as pl
import plotly
import plotly.plotly as py
import plotly.graph_objs as go

def cvxEDA(y, delta, tau0=2., tau1=0.7, delta_knot=10., alpha=8e-4, gamma=1e-2,
           solver=None, options={'reltol':1e-9}):

    # default options = same as Ledalab
    """CVXEDA Convex optimization approach to electrodermal activity processing
    Arguments:
       y: observed EDA signal (we recommend normalizing it: y = zscore(y))
       delta: sampling interval (in seconds) of y
       tau0: slow time constant of the Bateman function
       tau1: fast time constant of the Bateman function
       delta_knot: time between knots of the tonic spline function
       alpha: penalization for the sparse SMNA driver
       gamma: penalization for the tonic spline coefficients
       solver: sparse QP solver to be used, see cvxopt.solvers.qp
       options: solver options, see:
                http://cvxopt.org/userguide/coneprog.html#algorithm-parameters
                'reltol' = relative accuracy
                'abstol' = absolute accuracy
                'feastol' = tolerance for feasibility conditions

    Returns (see paper for details):
       r: phasic component
       p: sparse SMNA driver of phasic component
       t: tonic component
       l: coefficients of tonic spline
       d: offset and slope of the linear drift term
       e: model residuals
       obj: value of objective function being minimized (eq 15 of paper)

       from Greco et al. (2016). cvxEDA: A Convex Optimization Approach
        to Electrodermal Activity Processing, IEEE Transactions on Biomedical
        Engineering, 63(4): 797-804.
    """


    n = len(y)
    print(n)
    y = cv.matrix(y)
    #print(y)

    # bateman ARMA model
    a1 = 1./min(tau1, tau0) # a1 > a0
    a0 = 1./max(tau1, tau0)
    ar = np.array([(a1*delta + 2.) * (a0*delta + 2.), 2.*a1*a0*delta**2 - 8.,
        (a1*delta - 2.) * (a0*delta - 2.)]) / ((a1 - a0) * delta**2)
    ma = np.array([1., 2., 1.])

    # matrices for ARMA model
    i = np.arange(2, n)
    A = cv.spmatrix(np.tile(ar, (n-2,1)), np.c_[i,i,i], np.c_[i,i-1,i-2], (n,n))
    M = cv.spmatrix(np.tile(ma, (n-2,1)), np.c_[i,i,i], np.c_[i,i-1,i-2], (n,n))

    # spline
    delta_knot_s = int(round(delta_knot / delta))
    spl = np.r_[np.arange(1.,delta_knot_s), np.arange(delta_knot_s, 0., -1.)] # order 1
    spl = np.convolve(spl, spl, 'full')
    spl /= max(spl)
    # matrix of spline regressors
    i = np.c_[np.arange(-(len(spl)//2), (len(spl)+1)//2)] + np.r_[np.arange(0, n, delta_knot_s)]
    nB = i.shape[1]
    j = np.tile(np.arange(nB), (len(spl),1))
    p = np.tile(spl, (nB,1)).T
    valid = (i >= 0) & (i < n)
    B = cv.spmatrix(p[valid], i[valid], j[valid])

    # trend
    C = cv.matrix(np.c_[np.ones(n), np.arange(1., n+1.)/n])
    nC = C.size[1]

    # Solve the problem:
    # .5*(M*q + B*l + C*d - y)^2 + alpha*sum(A,1)*p + .5*gamma*l'*l
    # s.t. A*q >= 0

    old_options = cv.solvers.options.copy()
    cv.solvers.options.clear()
    cv.solvers.options.update(options)
    if solver == 'conelp':
        # Use conelp
        z = lambda m,n: cv.spmatrix([],[],[],(m,n))
        G = cv.sparse([[-A,z(2,n),M,z(nB+2,n)],[z(n+2,nC),C,z(nB+2,nC)],
                    [z(n,1),-1,1,z(n+nB+2,1)],[z(2*n+2,1),-1,1,z(nB,1)],
                    [z(n+2,nB),B,z(2,nB),cv.spmatrix(1.0, range(nB), range(nB))]])
        h = cv.matrix([z(n,1),.5,.5,y,.5,.5,z(nB,1)])
        c = cv.matrix([(cv.matrix(alpha, (1,n)) * A).T,z(nC,1),1,gamma,z(nB,1)])
        res = cv.solvers.conelp(c, G, h, dims={'l':n,'q':[n+2,nB+2],'s':[]})
        obj = res['primal objective']
    else:
        # Use qp
        Mt, Ct, Bt = M.T, C.T, B.T
        H = cv.sparse([[Mt*M, Ct*M, Bt*M], [Mt*C, Ct*C, Bt*C],
                    [Mt*B, Ct*B, Bt*B+gamma*cv.spmatrix(1.0, range(nB), range(nB))]])
        f = cv.matrix([(cv.matrix(alpha, (1,n)) * A).T - Mt*y,  -(Ct*y), -(Bt*y)])
        res = cv.solvers.qp(H, f, cv.spmatrix(-A.V, A.I, A.J, (n,len(f))),
                            cv.matrix(0., (n,1)), solver=solver)
        obj = res['primal objective'] + .5 * (y.T * y)
    cv.solvers.options.clear()
    cv.solvers.options.update(old_options)

    l = res['x'][-nB:]
    d = res['x'][n:n+nC]
    t = B*l + C*d
    q = res['x'][:n]
    p = A * q
    r = M * q
    e = y - r - t

    return (np.array(a).ravel() for a in (r, p, t, l, d, e, obj))

def extract_zip_format_filenames(working_dir):
    zip_list = []
    EDA_list = []
    HR_list = []
    tag_list = []

    for dirpath, dirnames, filenames in os.walk(working_dir): # goes through every file in working_dir and all subdirectories
    # name of current dirctory, directories inside current dir, and files inside current dir
        for filename in filenames:
            # for the current dir, for all filenames in that dir...
            if '.zip' in filename:
                # is string.zip in string filename?
                path_to_zip_file = os.path.join(dirpath, filename)
                zip_list.append(path_to_zip_file)
                zip_ref = zipfile.ZipFile(path_to_zip_file, 'r')
                zipfile_name = os.path.splitext(os.path.basename(path_to_zip_file))[0]

                # check if the zip archive has already been unzipped
                # zipfile_name is sensor number
                if not os.path.exists(zipfile_name):
                    os.mkdir(zipfile_name)
                    zip_ref.extractall(os.path.join(working_dir, zipfile_name))
                    zip_ref.close()

                # if the unzipped folder already exists, skip the unzipping process
                if os.path.exists(zipfile_name):
                    print('Zip archive ' + zipfile_name + ' is unzipped.')

                sensorNum = path_to_zip_file[-21:-4]
                print('Sensor num: ' + sensorNum)

                # Check current working directory.
                working_sub_dir = os.path.join(working_dir, sensorNum)

                eda_filepath = os.path.join(working_sub_dir, 'EDA.csv')
                if os.path.isfile(eda_filepath): # check if an EDA.csv file exists in the folder
                    eda_filename = working_dir + '/' + str(sensorNum) + '_EDA.csv'
                    os.rename(eda_filepath, eda_filename)
                    EDA_list.append(eda_filename)

                if os.path.isfile(os.path.join(working_sub_dir, 'HR.csv')): # check if a HR.csv file exists in the folder
                    hr_filename = working_dir + '/' + str(sensorNum) + '_HR.csv'
                    os.rename(working_sub_dir + '/' + 'HR.csv', hr_filename)
                    HR_list.append(hr_filename)

                if os.path.isfile(os.path.join(working_sub_dir, 'tags.csv')): # check if a tags.csv file exists in the folder
                    tag_filename = working_dir + '/' + str(sensorNum) + '_tags.csv'
                    os.rename(working_sub_dir + '/' + 'tags.csv', tag_filename)
                    tag_list.append(tag_filename)

                shutil.rmtree(working_sub_dir)

    return zip_list, EDA_list, HR_list, tag_list

def plot_results(y, r, p, t, l, d, e, obj, min_baseline, Fs):
    timing = pl.arange(1., len(y) + 1.) / (60 * 4) # minutes = divide by 240 = 60 seconds * 4 records/sec
    fig1 = pl.plot(timing, y) # y = total skin conductance record (phasic + tonic + noise)
    pl.setp(fig1, color='r', linewidth=0.5)
    pl.ylim(0, 0.25)
    pl.xlim(-1, max(timing) + 1)
    pl.title('Skin conductance')
    pl.ylabel('Raw EDA')
    pl.xlabel('Time (min)')
    pl.show()

    fig2 = pl.plot(timing, r) # r = phasic component
    pl.setp(fig1, color='b', linewidth=0.5)
    pl.ylim(0, 0.15)
    pl.xlim(-1, max(timing) + 1)
    pl.title('Phasic component')
    pl.ylabel('EDA')
    pl.xlabel('Time (min)')
    pl.show()


    bl = pd.DataFrame(y[:(min_baseline * 60 * Fs)]) # takes first three minutes of EDA record and uses them as baseline
    bL = bl.mean()
    baseline = pd.to_numeric(bL)


    start_record = int((min_baseline + 0.5) * 60 * Fs)

    activity1_timesteps = pd.DataFrame(y[start_record:(start_record + 1000)])
    activity1_mean = activity1_timesteps.mean()
    activity1 = pd.to_numeric(activity1_mean)

    activity2_timesteps = pd.DataFrame(y[(start_record + 1001):(start_record + 2000)])
    activity2_mean = activity2_timesteps.mean()
    activity2 = pd.to_numeric(activity2_mean)

    activity3_timesteps = pd.DataFrame(y[(start_record + 2001):(start_record + 3000)])
    activity3_mean = activity3_timesteps.mean()
    activity3 = pd.to_numeric(activity3_mean)

    activity4_timesteps = pd.DataFrame(y[(start_record + 3001):])
    activity4_mean = activity4_timesteps.mean()
    activity4 = pd.to_numeric(activity4_mean)

    pd_activity1 = (activity1 - baseline) / baseline * 100
    pd_activity2 = (activity2 - baseline) / baseline * 100
    pd_activity3 = (activity3 - baseline) / baseline * 100
    pd_activity4 = (activity4 - baseline) / baseline * 100


    activities = [pd_activity1.iloc[0], pd_activity2.iloc[0], pd_activity3.iloc[0], pd_activity4.iloc[0]]
    y_pos = [1,2,3,4]

    fig3 = pl.bar(y_pos, activities, align='center', alpha=0.9)
    pl.xticks(y_pos)
    pl.ylabel('% difference EDA - baseline')
    pl.xlabel('Activity number')
    pl.show()

def format_and_plot_data(working_dir, Fs, delta, min_baseline):

    # working_dir = '/Users/amorrison/Projects/handsensors/empaticadata'
    # Fs = 4
    # delta = 0.25
    # min_baseline = 3
    try:
        Fs = int(Fs)
        delta = float(delta)
        min_baseline = int(min_baseline)
    except:
        print('FS, delta and min_baseline must be floating point numbers')

    # changes to working directory
    os.chdir(working_dir)

    # check that we're in the right directory
    print("Is this your working directory?")
    print(os.getcwd())

    zip_list, EDA_list, HR_list, tag_list = extract_zip_format_filenames(working_dir)

    print('Parsed ' + str(len(zip_list)) + ' zip archives: ')

    print("Getting EDA data from these data folders/sensor numbers:")

    EDA_dataframe_list = []
    fullRecordTime = []
    idx = 0

    for EDA_file in EDA_list:
        print(EDA_file)
        # 1. read EDA.csv file
        eda_df = pd.read_csv(EDA_file, names=['timesteps'], header=3)
        # 2. append all EDA data into single list, separate columns
        EDA_dataframe_list.append(eda_df)

        # 2. extract initial timestamp from the sensor name
        initTimestamp = int(EDA_file[-25:-15])

        # 3. check that the timestamp is the right length
        checkTimestampLength = len(str(initTimestamp))

        # 4. throw error message if timestamp is too short/long
        if checkTimestampLength != 10:
            raise Exception('Error: not enough digits in timestamp')

    print("Number of timestamps: " + str(len(EDA_dataframe_list)))

    #extract timesteps column
    y = EDA_dataframe_list[0]
    y_list = list(y['timesteps'])

    r, p, t, l, d, e, obj = cvxEDA(y_list, 1./Fs)

    plot_results(y, r, p, t, l, d, e, obj, Fs, min_baseline)


if __name__=='__main__':
    working_dir, Fs, delta, min_baseline = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    format_and_plot_data(working_dir, Fs, delta, min_baseline)
