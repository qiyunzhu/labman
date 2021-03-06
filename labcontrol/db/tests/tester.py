#!/usr/bin/env python

from datetime import datetime
from itertools import chain, cycle
from random import shuffle
import re

import numpy as np
import click

from labcontrol.db.process import (
    SamplePlatingProcess, GDNAExtractionProcess, GDNAPlateCompressionProcess,
    LibraryPrep16SProcess, NormalizationProcess, QuantificationProcess,
    LibraryPrepShotgunProcess, PoolingProcess, SequencingProcess)
from labcontrol.db.user import User
from labcontrol.db.plate import PlateConfiguration, Plate
from labcontrol.db.equipment import Equipment
from labcontrol.db.composition import ReagentComposition
from labcontrol.db.sql_connection import TRN


@click.group()
def tester():
    pass


def get_samples():
    with TRN:
        TRN.add("SELECT sample_id FROM qiita.study_sample")
        return TRN.execute_fetchflatten()


def get_target_plate_configuration(is_96):
    plate_configs = list(PlateConfiguration.iter())
    plate_config = None

    if is_96:
        target = '96-well deep-well plate'
    else:
        target = '384-well microtiter plate'

    for pc in plate_configs:
        if pc.description == target:
            plate_config = pc
            break

    if plate_config is None:
        raise ValueError("Unable to identify a plate of type: %s" % target)

    return plate_config


def get_primer_plate(is_96):
    plates = [Plate(item['plate_id'])
              for item in Plate.list_plates(['primer'])]

    if is_96:
        # use different plates for 16S
        shuffle(plates)
        hit = None
        for plate in plates:
            pc = plate.plate_configuration
            if pc.description == '96-well microtiter plate':
                hit = plate
                break
        if hit is None:
            raise ValueError("Unable to identify a primer plate")
        return (hit, None)
    else:
        # not shuffling as the order of the plates implicitly matters
        hits = []
        for plate in plates:
            pc = plate.plate_configuration
            if pc.description == '384-well microtiter plate':
                if 'primer' in plate.external_id.lower():
                    hits.append(plate)
        hits = hits[:2]
        if len(hits) != 2:
            raise ValueError("Unable to identify two primer plates")
        return hits


def create_sample_plate_process(user, samples, is_96):
    plate_config = get_target_plate_configuration(is_96)
    num_rows = plate_config.num_rows
    num_cols = plate_config.num_columns
    sp_process = SamplePlatingProcess.create(
        user, plate_config, 'Test plate %s' % datetime.now())

    # Plate the samples
    for idx, sample in enumerate(samples):
        i = int(idx / num_cols) + 1
        j = (idx % num_cols) + 1

        # Make sure that the user didn't pass more samples than wells
        if i > num_rows:
            break

        sp_process.update_well(i, j, sample)

    sample_plate = sp_process.plate
    return sp_process, sample_plate


def create_gdna_extraction_process(user, plate):
    kingfisher = Equipment(11)
    epmotion = Equipment(6)
    epmotion_tool = Equipment(15)
    extraction_kit = ReagentComposition(1)
    ext_process = GDNAExtractionProcess.create(
        user, plate, kingfisher, epmotion, epmotion_tool, extraction_kit, 100,
        'GDNA test plate %s' % datetime.now())
    gdna_plate = ext_process.plates[0]
    return ext_process, gdna_plate


def create_amplicon_prep(user, plate):
    primer_plate, _ = get_primer_plate(is_96=True)
    epmotion = Equipment(6)
    master_mix = ReagentComposition(2)
    water_lot = ReagentComposition(3)
    epmotion_tool_tm300 = Equipment(16)
    epmotion_tool_tm50 = Equipment(17)
    amplicon_process = LibraryPrep16SProcess.create(
        user, plate, primer_plate, 'Amplicon test plate %s' % datetime.now(),
        epmotion, epmotion_tool_tm300, epmotion_tool_tm50, master_mix,
        water_lot, 75,)
    amplicon_plate = amplicon_process.plates[0]
    return amplicon_process, amplicon_plate


def create_compression_process(user, gdna_plates):
    comp_process = GDNAPlateCompressionProcess.create(
        user, gdna_plates, 'Compressed test plate %s' % datetime.now(),
        Equipment(6))
    compressed_plate = comp_process.plates[0]
    return comp_process, compressed_plate


def create_quantification_process(user, plate):
    plate_config = plate.plate_configuration
    concentrations = np.around(
        np.random.rand(plate_config.num_rows, plate_config.num_columns), 6)
    quant_process = QuantificationProcess.create(user, plate, concentrations)
    return quant_process


def create_pool_quantification_process(user, pools):
    concentrations = np.around(np.random.rand(len(pools)), 6)
    concentrations = [{'composition': p, 'concentration': c}
                      for p, c in zip(pools, concentrations)]
    return QuantificationProcess.create_manual(user, concentrations)


def create_normalization_process(user, quant_process):
    water = ReagentComposition(3)
    norm_process = NormalizationProcess.create(
        user, quant_process, water,
        'Normalized test plate %s' % datetime.now())
    norm_plate = norm_process.plates[0]
    return norm_process, norm_plate


def create_shotgun_process(user, norm_plate):
    kapa = ReagentComposition(4)
    stub = ReagentComposition(5)
    primer_a, primer_b = get_primer_plate(is_96=False)
    shotgun_process = LibraryPrepShotgunProcess.create(
        user, norm_plate, 'Test Shotgun Library %s' % datetime.now(), kapa,
        stub, 4000, primer_a, primer_b)
    shotgun_plate = shotgun_process.plates[0]
    return shotgun_process, shotgun_plate


def create_plate_pool_process(user, quant_process, plate, func_data):
    input_compositions = []
    echo = Equipment(8)
    for well in chain.from_iterable(plate.layout):
        if well is not None:
            input_compositions.append({
                'composition': well.composition, 'input_volume': 1,
                'percentage_of_output': 1/9.0})
    pool_process = PoolingProcess.create(
        user, quant_process, 'New test pool name %s' % datetime.now(),
        4, input_compositions, func_data, robot=echo)
    return pool_process


def create_pools_pool_process(user, quant_process, pools):
    input_compositions = [
        {'composition': p, 'input_volume': 1, 'percentage_of_output': 1/9.0}
        for p in pools]
    pool_process = PoolingProcess.create(
        user, quant_process, 'New pool name %s' % datetime.now(), 5,
        input_compositions, {"function": "amplicon_pool", "parameters": {}})
    return pool_process


def create_sequencing_process(user, pools):
    seq_process = SequencingProcess.create(
        user, pools, 'New sequencing run %s' % datetime.now(),
        'Run experiment %s' % datetime.now(), Equipment(18), 151, 151,
        User('admin@foo.bar'),
        contacts=[User('test@foo.bar'), User('demo@microbio.me')])
    return seq_process


def integration_tests_amplicon_workflow(user, samples):
    # Sample Plating
    sp_process, sample_plate = create_sample_plate_process(user, samples[:96],
                                                           is_96=True)
    # gDNA extraction
    ext_process, gdna_plate = create_gdna_extraction_process(
        user, sample_plate)
    # Amplicon library prep
    amplicon_process, amplicon_plate = create_amplicon_prep(user, gdna_plate)
    # Library plate quantification
    amplicon_quant_process = create_quantification_process(
        user, amplicon_plate)
    # Plate pooling process
    plate_pool_process = create_plate_pool_process(
        user, amplicon_quant_process, amplicon_plate,
        {'function': 'amplicon',
         'parameters': {"dna_amount": 240, "min_val": 1, "max_val": 15,
                        "blank_volume": 2, "robot": 6, "destination": 1}})
    # Quantify pools
    pool_quant_process = create_pool_quantification_process(
        user, [plate_pool_process.pool])
    # Create sequencing pool process
    seq_pool_process = create_pools_pool_process(
        user, pool_quant_process, [plate_pool_process.pool])
    # Sequencing process
    seq_process = create_sequencing_process(user, [seq_pool_process.pool])
    return seq_process


def integration_tests_shotgun_workflow(user, samples):
    # Sample Plating
    sp_process, sample_plate = create_sample_plate_process(user, samples[:96])
    # gDNA extraction
    ext_process, gdna_plate = create_gdna_extraction_process(
        user, sample_plate)
    # gDNA compression
    comp_process, compressed_plate = create_compression_process(
        user, [gdna_plate])
    # gDNA compressed quantification
    gdna_comp_quant_process = create_quantification_process(
        user, compressed_plate)
    # Normalization process
    norm_process, norm_plate = create_normalization_process(
        user, gdna_comp_quant_process)
    # Library prep shotgun
    shotgun_process, shotgun_plate = create_shotgun_process(user, norm_plate)
    # Quantify library plate
    shotgun_quant_process = create_quantification_process(user, shotgun_plate)
    # Pooling process
    pool_process = create_plate_pool_process(
        user, shotgun_quant_process, shotgun_plate,
        {'function': 'equal', 'parameters': {'total_vol': 60, 'size': 500}})
    # Sequencing process
    seq_process = create_sequencing_process(user, [pool_process.pool])
    return seq_process


@tester.command("integration_tests")
def integration_tests():
    """Creates one amplicon and one shotgun workflow from plating to sequencing
    """
    samples = get_samples()
    user = User('test@foo.bar')

    amplicon_seq_process = integration_tests_amplicon_workflow(user, samples)
    obs = amplicon_seq_process.generate_sample_sheet()
    res = re.match(EXP_AMPLICON_SAMPLE_SHEET, obs)
    if res is None:
        raise ValueError(
            'Amplicon sample sheet does not match expected regex:\n%s' % obs)

    # ToDo: #327
    # shotgun_seq_process = integration_tests_shotgun_workflow(user, samples)
    # obs = shotgun_seq_process.generate_sample_sheet()
    # res = re.match(EXP_SHOTGUN_SAMPLE_SHEET, obs)
    # if res is None:
    #     raise ValueError(
    #         'Shotgun sample sheet does not match expected regex:\n%s' % obs)


def stress_tests_amplicon_workflow(user, samples, num_plates):
    print('Amplicon workflow', flush=True)
    # Sample Plating
    sp_processes = []
    sample_iter = cycle(samples)
    with click.progressbar(range(num_plates),
                           label='\tSample plating process') as bar:
        for i in bar:
            plate_samples = [next(sample_iter) for _ in range(88)]
            sp_processes.append(
                create_sample_plate_process(user, plate_samples, is_96=True))

    # gDNA extraction
    ext_processes = []
    with click.progressbar(sp_processes,
                           label='\tgDNA extraction process') as bar:
        for _, sp in bar:
            ext_processes.append(create_gdna_extraction_process(user, sp))

    # Amplicon library prep
    amplicon_processes = []
    with click.progressbar(ext_processes,
                           label='\tAmplicon library prep process') as bar:
        for _, gp in bar:
            amplicon_processes.append(create_amplicon_prep(user, gp))

    # Library plate quantification
    amplicon_quant_processes = []
    with click.progressbar(
            amplicon_processes,
            label='\tAmplicon library quantification process') as bar:
        for _, ap in bar:
            amplicon_quant_processes.append(
                create_quantification_process(user, ap))

    # Plate pooling process
    plate_pool_processes = []
    with click.progressbar(zip(amplicon_quant_processes, amplicon_processes),
                           label='\tPlate pooling process') as bar:
        for aqp, (_, ap) in bar:
            plate_pool_processes.append(
                create_plate_pool_process(
                    user, aqp, ap,
                    {'function': 'amplicon',
                     'parameters': {"dna_amount": 240, "min_val": 1,
                                    "max_val": 15, "blank_volume": 2,
                                    "robot": 6, "destination": 1}}))

    # Quantify pools
    pool_quant_processes = []
    with click.progressbar(
            range(0, num_plates, 8),
            label='\tPlate pool quantification process') as bar:
        for pos in bar:
            pools = [ppp.pool for ppp in plate_pool_processes[pos:pos + 8]]
            pool_quant_processes.append(
                (create_pool_quantification_process(user, pools), pools))

    # Create sequencing pool process
    seq_pool_processes = []
    with click.progressbar(pool_quant_processes,
                           label='\tSequencing pool process') as bar:
        for pqp, pools in bar:
            seq_pool_processes.append(create_pools_pool_process(
                user, pqp, pools))

    # Sequencing process
    with click.progressbar(seq_pool_processes,
                           label='\tSequencing process') as bar:
        for sqp in bar:
            create_sequencing_process(user, [sqp.pool])


def stress_tests_shotgun_workflow(user, samples, num_plates):
    print('Shotgun workflow', flush=True)
    # Sample Plating
    sp_processes = []
    sample_iter = cycle(samples)
    cutoff = 352
    with click.progressbar(range(num_plates),
                           label='\tSample plating process') as bar:
        for i in bar:
            plate_samples = [next(sample_iter) for i in range(cutoff)]
            sp_processes.append(create_sample_plate_process(
                user, plate_samples, is_96=False))

    # gDNA extraction
    ext_processes = []
    with click.progressbar(sp_processes,
                           label='\tgDNA extraction process') as bar:
        for _, sp in bar:
            ext_processes.append(create_gdna_extraction_process(user, sp))

    # gDNA compression
    comp_processes = []
    with click.progressbar(range(0, num_plates, 4),
                           label='\tgDNA compression process') as bar:
        for pos in bar:
            plates = [gp for _, gp in ext_processes[pos:pos + 4]]
            comp_processes.append(create_compression_process(user, plates))

    # gDNA compressed quantification
    gdna_comp_quant_processes = []
    with click.progressbar(
            comp_processes,
            label='\tcompressed gDNA quantification process') as bar:
        for _, cp in bar:
            gdna_comp_quant_processes.append(
                create_quantification_process(user, cp))

    # Normalization process
    norm_processes = []
    with click.progressbar(gdna_comp_quant_processes,
                           label='\tgDNA normalization process') as bar:
        for qp in bar:
            norm_processes.append(create_normalization_process(user, qp))

    # Library prep shotgun
    shotgun_processes = []
    with click.progressbar(norm_processes,
                           label='\tShotgun library prep process') as bar:
        for _, norm_plate in bar:
            shotgun_processes.append(create_shotgun_process(user, norm_plate))

    # Quantify library plate
    shotgun_quant_processes = []
    with click.progressbar(
            shotgun_processes,
            label='\tShotgun library quantification process') as bar:
        for _, sp in bar:
            shotgun_quant_processes.append(
                create_quantification_process(user, sp))

    # Pooling process
    pool_processes = []
    with click.progressbar(zip(shotgun_quant_processes, shotgun_processes),
                           label='\tPooling process') as bar:
        for sqp, (_, sp) in bar:
            pool_processes.append(
                create_plate_pool_process(
                    user, sqp, sp, {'function': 'equal',
                                    'parameters': {'total_vol': 60,
                                                   'size': 500}}))

    # Sequencing process
    with click.progressbar(pool_processes,
                           label='\tSequencing process') as bar:
        for pool_process in bar:
            create_sequencing_process(user, [pool_process.pool])


@tester.command("stress_tests")
@click.option('--num_plates_amplicon', required=False,
              type=click.IntRange(0, None),
              default=0, show_default=True,
              help='Number of plates to create per workflow')
@click.option('--num_plates_shotgun', required=False,
              type=click.IntRange(0, None),
              default=0, show_default=True,
              help='Number of plates to create per workflow')
@click.option('--user', required=False, type=str,
              default='test@foo.bar', show_default=True)
def stress_tests(num_plates_amplicon, num_plates_shotgun, user):
    """Creates num_plates plates and complete the amplicon/shotgun workflow
    """
    samples = get_samples()
    user = User(user)

    if num_plates_amplicon:
        stress_tests_amplicon_workflow(user, samples,
                                       num_plates=num_plates_amplicon)
    if num_plates_shotgun:
        stress_tests_shotgun_workflow(user, samples,
                                      num_plates=num_plates_shotgun)


EXP_AMPLICON_SAMPLE_SHEET = r"""# PI,Admin,admin@foo.bar
# Contact,Demo,Dude
# Contact emails,demo@microbio.me,test@foo.bar
\[Header\]
IEMFileVersion,4
Investigator Name,Admin
Experiment Name,Run experiment \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}.\d{6}
Date,\d{4}-\d{2}-\d{2} \d{2}:\d{2}
Workflow,GenerateFASTQ
Application,FASTQ Only
Assay,TruSeq HT
Description,
Chemistry,Amplicon

\[Reads\]
151
151

\[Settings\]
ReverseComplement,0
Adapter,AGATCGGAAGAGCACACGTCTGAACTCCAGTCA
AdapterRead2,AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT

\[Data\]
Sample_ID,Sample_Name,Sample_Plate,Sample_Well,I7_Index_ID,index,I5_Index_ID,index2,Sample_Project,Well_Description,,
New_pool_name_\d{4}-\d{2}-\d{2}_\d{2}_\d{2}_\d{2}_\d{6},,,,,NNNNNNNNNNNN,,,,\d+,,,"""  # noqa


if __name__ == '__main__':
    tester()
