from itertools import groupby
from typing import List, Optional

from mev_inspect.schemas.arbitrage import Arbitrage
from mev_inspect.schemas.classified_traces import (
    ClassifiedTrace,
    Classification,
)
from mev_inspect.schemas.swaps import Swap
from mev_inspect.schemas.transfers import Transfer
from mev_inspect.transfers import (
    get_child_transfers,
    filter_transfers,
    remove_child_transfers,
)


UNISWAP_V2_PAIR_ABI_NAME = "UniswapV2Pair"
UNISWAP_V3_POOL_ABI_NAME = "UniswapV3Pool"


def get_arbitrages(traces: List[ClassifiedTrace]) -> List[Arbitrage]:
    get_transaction_hash = lambda t: t.transaction_hash
    traces_by_transaction = groupby(
        sorted(traces, key=get_transaction_hash),
        key=get_transaction_hash,
    )

    all_arbitrages = []

    for _, transaction_traces in traces_by_transaction:
        all_arbitrages += _get_arbitrages_for_transaction(
            list(transaction_traces),
        )

    return all_arbitrages


def _get_arbitrages_for_transaction(
    traces: List[ClassifiedTrace],
) -> List[Arbitrage]:
    swaps = _get_swaps(traces)

    if len(swaps) > 1:
        return _get_arbitrages_from_swaps(swaps)

    return []


def _get_arbitrages_from_swaps(swaps: List[Swap]) -> List[Arbitrage]:
    pool_addresses = {swap.pool_address for swap in swaps}

    all_arbitrages = []

    for index, first_swap in enumerate(swaps):
        other_swaps = swaps[:index] + swaps[index + 1 :]

        if first_swap.from_address not in pool_addresses:
            arbitrage = _get_arbitrage_starting_with_swap(first_swap, other_swaps)

            if arbitrage is not None:
                all_arbitrages.append(arbitrage)

    return all_arbitrages


def _get_arbitrage_starting_with_swap(
    start_swap: Swap,
    other_swaps: List[Swap],
) -> Optional[Arbitrage]:
    swap_path = [start_swap]

    current_address = start_swap.to_address
    current_token = start_swap.token_out_address

    while True:
        swaps_from_current_address = []

        for swap in other_swaps:
            if (
                swap.pool_address == current_address
                and swap.token_in_address == current_token
            ):
                swaps_from_current_address.append(swap)

        if len(swaps_from_current_address) == 0:
            return None

        if len(swaps_from_current_address) > 1:
            raise RuntimeError("todo")

        latest_swap = swaps_from_current_address[0]
        swap_path.append(latest_swap)

        current_address = latest_swap.to_address
        current_token = latest_swap.token_out_address

        if (
            current_address == start_swap.from_address
            and current_token == start_swap.token_in_address
        ):

            start_amount = start_swap.token_in_amount
            end_amount = latest_swap.token_out_amount
            profit_amount = end_amount - start_amount

            return Arbitrage(
                swaps=swap_path,
                account_address=start_swap.from_address,
                profit_token_address=start_swap.token_in_address,
                start_amount=start_amount,
                end_amount=end_amount,
                profit_amount=profit_amount,
            )

    return None


def _get_swaps(traces: List[ClassifiedTrace]) -> List[Swap]:
    ordered_traces = list(sorted(traces, key=lambda t: t.trace_address))

    swaps: List[Swap] = []
    prior_transfers: List[Transfer] = []

    for trace in ordered_traces:
        if trace.classification == Classification.transfer:
            prior_transfers.append(Transfer.from_trace(trace))

        elif trace.classification == Classification.swap:
            child_transfers = get_child_transfers(trace.trace_address, traces)
            swap = _build_swap(
                trace,
                remove_child_transfers(prior_transfers),
                remove_child_transfers(child_transfers),
            )

            if swap is not None:
                swaps.append(swap)

    return swaps


def _build_swap(
    trace: ClassifiedTrace,
    prior_transfers: List[Transfer],
    child_transfers: List[Transfer],
) -> Optional[Swap]:
    if trace.abi_name == UNISWAP_V2_PAIR_ABI_NAME:
        return _parse_uniswap_v2_swap(trace, prior_transfers, child_transfers)
    elif trace.abi_name == UNISWAP_V3_POOL_ABI_NAME:
        return _parse_uniswap_v3_swap(trace, child_transfers)

    return None


def _parse_uniswap_v3_swap(
    trace: ClassifiedTrace,
    child_transfers: List[Transfer],
) -> Optional[Swap]:
    pool_address = trace.to_address
    recipient_address = (
        trace.inputs["recipient"]
        if trace.inputs is not None and "recipient" in trace.inputs
        else trace.from_address
    )

    transfers_to_pool = filter_transfers(child_transfers, to_address=pool_address)
    transfers_from_pool_to_recipient = filter_transfers(
        child_transfers, to_address=recipient_address, from_address=pool_address
    )

    if len(transfers_to_pool) == 0:
        return None

    if len(transfers_from_pool_to_recipient) != 1:
        return None

    transfer_in = transfers_to_pool[-1]
    transfer_out = transfers_from_pool_to_recipient[0]

    return Swap(
        abi_name=UNISWAP_V3_POOL_ABI_NAME,
        transaction_hash=trace.transaction_hash,
        trace_address=trace.trace_address,
        pool_address=pool_address,
        from_address=transfer_in.from_address,
        to_address=transfer_out.to_address,
        token_in_address=transfer_in.token_address,
        token_in_amount=transfer_in.amount,
        token_out_address=transfer_out.token_address,
        token_out_amount=transfer_out.amount,
    )


def _parse_uniswap_v2_swap(
    trace: ClassifiedTrace,
    prior_transfers: List[Transfer],
    child_transfers: List[Transfer],
) -> Optional[Swap]:

    pool_address = trace.to_address
    recipient_address = (
        trace.inputs["to"]
        if trace.inputs is not None and "to" in trace.inputs
        else trace.from_address
    )

    transfers_to_pool = filter_transfers(prior_transfers, to_address=pool_address)
    transfers_from_pool_to_recipient = filter_transfers(
        child_transfers, to_address=recipient_address, from_address=pool_address
    )

    if len(transfers_to_pool) == 0:
        return None

    if len(transfers_from_pool_to_recipient) != 1:
        return None

    transfer_in = transfers_to_pool[-1]
    transfer_out = transfers_from_pool_to_recipient[0]

    return Swap(
        abi_name=UNISWAP_V2_PAIR_ABI_NAME,
        transaction_hash=trace.transaction_hash,
        trace_address=trace.trace_address,
        pool_address=pool_address,
        from_address=transfer_in.from_address,
        to_address=transfer_out.to_address,
        token_in_address=transfer_in.token_address,
        token_in_amount=transfer_in.amount,
        token_out_address=transfer_out.token_address,
        token_out_amount=transfer_out.amount,
    )
